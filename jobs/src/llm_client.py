"""OpenAI-compatible LLM client for PDF metadata and data-table extraction.

This replaces the upstream Gemini-only client for single-key self-hosting. All
calls go to an OpenAI-compatible ``/chat/completions`` endpoint (OpenCode Go in
the homelab deployment), so the worker needs no Gemini, Anthropic, or sandbox
credentials to ingest a paper.
"""

import asyncio
import base64
import json
import logging
import os
import random
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Type, TypeVar

import httpx
import pymupdf
from openai import (
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from src.prompts import (
    EXTRACT_COLS_INSTRUCTION,
    EXTRACT_METADATA_PROMPT_TEMPLATE,
    SYSTEM_INSTRUCTIONS_CACHE,
)
from src.schemas import (
    CellEntry,
    DataTableCellValue,
    DataTableRow,
    Highlights,
    InstitutionsKeywords,
    PaperMetadataExtraction,
    ResponseCitation,
    SummaryAndCitations,
    TitleAuthorsAbstract,
)
from src.utils import retry_llm_operation, time_it

logger = logging.getLogger(__name__)

DEFAULT_CHAT_MODEL = os.getenv("OPENAI_DEFAULT_MODEL", "deepseek-v4.1-flash")
FAST_CHAT_MODEL = os.getenv("OPENAI_FAST_MODEL", "glm-5.3-flash")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("OPENCODE_GO_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL") or None
METADATA_FALLBACK_MODEL = os.getenv("OPENAI_FALLBACK_MODEL", DEFAULT_CHAT_MODEL)


def _extra_headers() -> Optional[Dict[str, str]]:
    raw = os.getenv("OPENAI_EXTRA_HEADERS")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
    except (TypeError, ValueError):
        logger.warning("OPENAI_EXTRA_HEADERS is not valid JSON; ignoring it")
    return None


def _create_openai_client(timeout: Optional[float] = None) -> AsyncOpenAI:
    if not OPENAI_API_KEY:
        raise ValueError(
            "OPENAI_API_KEY or OPENCODE_GO_API_KEY environment variable is required"
        )
    kwargs: Dict[str, Any] = {
        "api_key": OPENAI_API_KEY,
        "base_url": OPENAI_BASE_URL,
        "default_headers": _extra_headers(),
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    return AsyncOpenAI(**kwargs)


def _strict_json_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Put a JSON schema in the form OpenAI strict structured outputs require."""
    defs = schema.get("$defs", {})

    def strict(node: Any) -> Any:
        if isinstance(node, list):
            return [strict(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node and len(node) > 1:
            target = defs[node["$ref"].split("/")[-1]]
            node = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
        result: Dict[str, Any] = {}
        for key, value in node.items():
            if key == "default":
                continue
            if key in ("properties", "$defs"):
                result[key] = {name: strict(sub) for name, sub in value.items()}
            else:
                result[key] = strict(value)
        if "properties" in node:
            result["additionalProperties"] = False
            result["required"] = list(node["properties"])
        return result

    return strict(schema)


def _response_format(schema: Type[BaseModel]) -> Dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema.__name__,
            "strict": True,
            "schema": _strict_json_schema(schema.model_json_schema()),
        },
    }


def _is_retryable(e: Exception) -> bool:
    if isinstance(e, (httpx.TimeoutException, APITimeoutError, RateLimitError, InternalServerError)):
        return True
    if isinstance(e, APIStatusError):
        return e.status_code in {408, 409, 429, 500, 502, 503, 504}
    return False


def _format_api_error(e: Exception) -> Any:
    return getattr(e, "message", None) or str(e)


def _pdf_text(path: str) -> str:
    document = pymupdf.open(path)
    try:
        return "\n".join(page.get_text() for page in document)
    finally:
        document.close()


class JSONParser:
    @staticmethod
    def validate_and_extract_json(json_data: str) -> dict:
        if not json_data or not isinstance(json_data, str):
            raise ValueError("Invalid input: empty or non-string data")

        json_data = json_data.strip()
        try:
            return json.loads(json_data)
        except json.JSONDecodeError:
            pass

        if "```" in json_data:
            code_blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", json_data)
            for block in code_blocks:
                block = block.strip()
                block = re.sub(r"}\s+\w+\s+}", "}}", block)
                block = re.sub(r"}\s+\w+\s+,", "},", block)
                try:
                    return json.loads(block)
                except json.JSONDecodeError:
                    continue

        raise ValueError(
            "Could not extract valid JSON from the provided string. "
            "Please ensure the response contains proper JSON format."
        )


T = TypeVar("T", bound=BaseModel)


class AsyncLLMClient:
    """Small async wrapper around an OpenAI-compatible chat-completions API."""

    DEFAULT_TIMEOUT = 120.0
    PDF_TIMEOUT = 180.0

    def __init__(self, api_key: str, default_model: Optional[str] = None):
        self.api_key = api_key
        self.default_model = default_model or DEFAULT_CHAT_MODEL

    def _create_client(self, timeout: Optional[float] = None) -> AsyncOpenAI:
        return _create_openai_client(timeout=timeout)

    async def _close_client(self, client: AsyncOpenAI) -> None:
        try:
            await client.close()
        except Exception as e:
            logger.debug(f"Error closing OpenAI client: {e}")

    async def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        """Conservative token estimate.

        The endpoint exposes no free token-count API, and the configured model
        has a 1M-token window, so a deliberately high estimate is enough to
        keep the truncation guard honest without another network call.
        """
        return max(1, len(text) // 3)

    async def create_cache(
        self,
        cache_content: str,
        client: AsyncOpenAI,
        model: Optional[str] = None,
    ) -> Optional[str]:
        # Context caching is a Gemini-only feature in upstream. The inline path
        # already carries the paper text, so there is nothing to create here.
        return None

    async def create_file_cache(
        self,
        file_path: str,
        client: AsyncOpenAI,
        system_instructions: Optional[str] = None,
    ) -> Optional[str]:
        return None

    async def generate_content(
        self,
        prompt: str,
        image_bytes: Optional[bytes] = None,
        image_mime_type: Optional[str] = None,
        cache_key: Optional[str] = None,
        model: Optional[str] = None,
        schema: Optional[Type[BaseModel]] = None,
        file_path: Optional[str] = None,
        max_retries: int = 3,
        base_delay: float = 1.0,
        client: Optional[AsyncOpenAI] = None,
    ) -> str:
        client = client or self._create_client()
        if file_path:
            paper_text = await asyncio.to_thread(_pdf_text, file_path)
            prompt = f"Paper Content:\n\n{paper_text}\n\n{prompt}"

        if image_bytes:
            mime = image_mime_type or "image/png"
            encoded = base64.b64encode(image_bytes).decode("utf-8")
            content: Any = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{encoded}"},
                },
            ]
        else:
            content = prompt

        kwargs: Dict[str, Any] = {}
        if schema is not None:
            kwargs["response_format"] = _response_format(schema)
        reasoning_effort = os.getenv("OPENAI_REASONING_EFFORT")
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort

        last_exception: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                response = await client.chat.completions.create(
                    model=model or self.default_model,
                    messages=[{"role": "user", "content": content}],
                    **kwargs,
                )
                text = response.choices[0].message.content
                if text:
                    return text
                raise ValueError("No content generated from LLM response")
            except Exception as e:
                last_exception = e
                if not _is_retryable(e):
                    logger.error(
                        "Non-retryable LLM API error for generate_content: %s",
                        _format_api_error(e),
                    )
                    break
                if attempt < max_retries:
                    backoff = base_delay * (2**attempt) * (0.5 + 0.5 * random.random())
                    logger.warning(
                        "LLM API error (attempt %d/%d): %s. Retrying in %.2fs",
                        attempt + 1,
                        max_retries + 1,
                        _format_api_error(e),
                        backoff,
                    )
                    await asyncio.sleep(backoff)

        raise last_exception or ValueError("Failed to generate content after all retries")

    async def generate_structured(
        self,
        prompt: str,
        schema: Type[T],
        client: Optional[AsyncOpenAI],
        model: Optional[str] = None,
        file_path: Optional[str] = None,
        cache_key: Optional[str] = None,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> T:
        client = client or self._create_client()
        if file_path:
            paper_text = await asyncio.to_thread(_pdf_text, file_path)
            prompt = f"Paper Content:\n\n{paper_text}\n\n{prompt}"

        messages: List[Dict[str, Any]] = [{"role": "user", "content": prompt}]
        response_format = _response_format(schema)

        last_exception: Optional[Exception] = None
        last_raw: Optional[str] = None
        for attempt in range(max_retries + 1):
            if attempt > 0 and last_raw is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous response could not be parsed as valid JSON. "
                            "Respond with ONLY a single JSON object matching the schema — "
                            "no prose, no markdown code fences, and properly escape any "
                            "quotes inside string values."
                        ),
                    }
                )
            try:
                request_kwargs: Dict[str, Any] = {"response_format": response_format}
                reasoning_effort = os.getenv("OPENAI_REASONING_EFFORT")
                if reasoning_effort:
                    request_kwargs["reasoning_effort"] = reasoning_effort
                response = await client.chat.completions.create(
                    model=model or self.default_model,
                    messages=messages,
                    **request_kwargs,
                )
                last_raw = response.choices[0].message.content
                if not last_raw:
                    raise ValueError("No content generated from LLM response")
                parsed = JSONParser.validate_and_extract_json(last_raw)
                return schema.model_validate(parsed)
            except (ValueError, ValidationError) as e:
                last_exception = e
                if attempt < max_retries:
                    backoff = base_delay * (2**attempt) * (0.5 + 0.5 * random.random())
                    logger.warning(
                        "Structured parse failed for %s (attempt %d/%d): %s. "
                        "Retrying in %.2fs",
                        schema.__name__,
                        attempt + 1,
                        max_retries + 1,
                        e,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
            except Exception as e:
                last_exception = e
                if not _is_retryable(e):
                    logger.error(
                        "Non-retryable LLM API error for generate_structured (%s): %s",
                        schema.__name__,
                        _format_api_error(e),
                    )
                    break
                if attempt < max_retries:
                    backoff = base_delay * (2**attempt) * (0.5 + 0.5 * random.random())
                    logger.warning(
                        "LLM API failed (attempt %d/%d): %s. Retrying in %.2fs",
                        attempt + 1,
                        max_retries + 1,
                        _format_api_error(e),
                        backoff,
                    )
                    await asyncio.sleep(backoff)

        raise last_exception or ValueError(
            f"Failed to generate structured content for {schema.__name__}"
        )


class PaperOperations(AsyncLLMClient):
    """Paper-specific extraction operations on top of the generic client."""

    async def count_metadata_prompt_tokens(self, paper_content: str) -> int:
        return await self.count_tokens(paper_content, model=FAST_CHAT_MODEL)

    async def _extract_single_metadata_field(
        self,
        model: Type[T],
        paper_content: str,
        schema: Type[BaseModel],
        status_callback: Callable[[str], None],
        client: AsyncOpenAI,
        cache_key: Optional[str] = None,
        llm_model: Optional[str] = None,
    ) -> T:
        prompt = EXTRACT_METADATA_PROMPT_TEMPLATE
        if paper_content and not cache_key:
            prompt = f"Paper Content:\n\n{paper_content}\n\n{prompt}"

        instance = await self.generate_structured(
            prompt,
            schema=schema,
            cache_key=cache_key,
            client=client,
            model=llm_model,
        )

        if model == SummaryAndCitations:
            status_callback(
                f"Compiled with {len(getattr(instance, 'summary_citations', []))} citations"
            )
        elif model == InstitutionsKeywords:
            keywords = getattr(instance, "keywords", [])
            institutions = getattr(instance, "institutions", [])
            if keywords:
                status_callback(f"Building on {keywords[0]} context")
            elif institutions:
                status_callback(f"Adding context from institution: {institutions[0]}")
            else:
                status_callback("Processing without keyword data")
        elif model == Highlights:
            status_callback(
                f"Formulated {len(getattr(instance, 'highlights', []))} annotations"
            )
        elif model == TitleAuthorsAbstract:
            title = getattr(instance, "title", "")
            status_callback(f"Reading {title if title else 'untitled paper'}")
        else:
            status_callback(f"Successfully extracted {model.__name__}")

        return instance

    def _with_model_fallback(
        self, extract: Callable[..., Awaitable[T]], schema: Type[T], job_id: str
    ) -> Callable[..., Awaitable[T]]:
        async def run(**kwargs: Any) -> T:
            try:
                return await extract(**kwargs)
            except Exception as e:
                if not _is_retryable(e):
                    raise
                logger.warning(
                    "Metadata step %s failed on %s for job %s: %s. Retrying on %s",
                    schema.__name__,
                    kwargs.get("llm_model"),
                    job_id,
                    _format_api_error(e),
                    METADATA_FALLBACK_MODEL,
                )
            return await self._extract_with_openai(schema, kwargs["paper_content"])

        return run

    async def _extract_with_openai(self, schema: Type[T], paper_content: str) -> T:
        client = self._create_client()
        try:
            return await self.generate_structured(
                f"Paper Content:\n\n{paper_content}\n\n{EXTRACT_METADATA_PROMPT_TEMPLATE}",
                schema=schema,
                client=client,
                model=METADATA_FALLBACK_MODEL,
            )
        finally:
            await self._close_client(client)

    @retry_llm_operation(max_retries=3, delay=1.0)
    async def extract_title_authors_abstract(
        self,
        paper_content: str,
        status_callback: Callable[[str], None],
        client: AsyncOpenAI,
        cache_key: Optional[str] = None,
        llm_model: Optional[str] = None,
    ) -> TitleAuthorsAbstract:
        return await self._extract_single_metadata_field(
            model=TitleAuthorsAbstract,
            cache_key=cache_key,
            schema=TitleAuthorsAbstract,
            paper_content=paper_content,
            status_callback=status_callback,
            client=client,
            llm_model=llm_model,
        )

    @retry_llm_operation(max_retries=3, delay=1.0)
    async def extract_institutions_keywords(
        self,
        paper_content: str,
        status_callback: Callable[[str], None],
        client: AsyncOpenAI,
        cache_key: Optional[str] = None,
        llm_model: Optional[str] = None,
    ) -> InstitutionsKeywords:
        return await self._extract_single_metadata_field(
            model=InstitutionsKeywords,
            cache_key=cache_key,
            schema=InstitutionsKeywords,
            paper_content=paper_content,
            status_callback=status_callback,
            client=client,
            llm_model=llm_model,
        )

    @retry_llm_operation(max_retries=3, delay=1.0)
    async def extract_summary_and_citations(
        self,
        paper_content: str,
        status_callback: Callable[[str], None],
        client: AsyncOpenAI,
        cache_key: Optional[str] = None,
        llm_model: Optional[str] = None,
    ) -> SummaryAndCitations:
        return await self._extract_single_metadata_field(
            model=SummaryAndCitations,
            cache_key=cache_key,
            schema=SummaryAndCitations,
            paper_content=paper_content,
            status_callback=status_callback,
            client=client,
            llm_model=llm_model,
        )

    @retry_llm_operation(max_retries=3, delay=1.0)
    async def extract_highlights(
        self,
        paper_content: str,
        status_callback: Callable[[str], None],
        client: AsyncOpenAI,
        cache_key: Optional[str] = None,
        llm_model: Optional[str] = None,
    ) -> Highlights:
        return await self._extract_single_metadata_field(
            model=Highlights,
            cache_key=cache_key,
            schema=Highlights,
            paper_content=paper_content,
            status_callback=status_callback,
            client=client,
            llm_model=llm_model,
        )

    async def extract_paper_metadata(
        self,
        paper_content: str,
        job_id: str,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> PaperMetadataExtraction:
        status_callback = status_callback or (lambda _status: None)
        async with time_it("Extracting paper metadata from LLM", job_id=job_id):
            extraction_model = FAST_CHAT_MODEL
            client = self._create_client()
            try:
                async with time_it(
                    "Running all metadata extraction tasks concurrently", job_id=job_id
                ):
                    tasks = [
                        asyncio.create_task(
                            self._with_model_fallback(
                                self.extract_title_authors_abstract,
                                TitleAuthorsAbstract,
                                job_id,
                            )(
                                paper_content=paper_content,
                                cache_key=None,
                                status_callback=status_callback,
                                client=client,
                                llm_model=extraction_model,
                            )
                        ),
                        asyncio.create_task(
                            self._with_model_fallback(
                                self.extract_institutions_keywords,
                                InstitutionsKeywords,
                                job_id,
                            )(
                                paper_content=paper_content,
                                cache_key=None,
                                status_callback=status_callback,
                                client=client,
                                llm_model=extraction_model,
                            )
                        ),
                        asyncio.create_task(
                            self._with_model_fallback(
                                self.extract_summary_and_citations,
                                SummaryAndCitations,
                                job_id,
                            )(
                                paper_content=paper_content,
                                cache_key=None,
                                status_callback=status_callback,
                                client=client,
                                llm_model=extraction_model,
                            )
                        ),
                        asyncio.create_task(
                            self._with_model_fallback(
                                self.extract_highlights,
                                Highlights,
                                job_id,
                            )(
                                paper_content=paper_content,
                                cache_key=None,
                                status_callback=status_callback,
                                client=client,
                                llm_model=extraction_model,
                            )
                        ),
                    ]
                    results = await asyncio.gather(
                        *[asyncio.shield(task) for task in tasks],
                        return_exceptions=True,
                    )

                (
                    title_authors_abstract,
                    institutions_keywords,
                    summary_and_citations,
                    highlights,
                ) = results

                failed = [
                    (label, result)
                    for label, result in zip(
                        (
                            "title_authors_abstract",
                            "institutions_keywords",
                            "summary_and_citations",
                            "highlights",
                        ),
                        results,
                    )
                    if isinstance(result, BaseException)
                ]
                for label, result in failed:
                    logger.error(
                        "Metadata subtask %r failed for job %s: %s",
                        label,
                        job_id,
                        result,
                        exc_info=result,
                    )

                if isinstance(title_authors_abstract, BaseException):
                    raise title_authors_abstract

                return PaperMetadataExtraction(
                    title=getattr(title_authors_abstract, "title", ""),
                    authors=getattr(title_authors_abstract, "authors", []),
                    abstract=getattr(title_authors_abstract, "abstract", ""),
                    institutions=getattr(institutions_keywords, "institutions", []),
                    keywords=getattr(institutions_keywords, "keywords", []),
                    summary=getattr(summary_and_citations, "summary", ""),
                    summary_citations=getattr(
                        summary_and_citations, "summary_citations", []
                    ),
                    highlights=getattr(highlights, "highlights", []),
                    publish_date=getattr(title_authors_abstract, "publish_date", None),
                )
            except Exception as e:
                logger.error(f"Error extracting metadata: {e}", exc_info=True)
                status_callback(f"Error during metadata extraction: {e}")
                raise ValueError(f"Failed to extract metadata: {str(e)}")
            finally:
                await self._close_client(client)

    async def extract_data_table(
        self,
        columns: List[str],
        file_path: str,
        paper_id: str,
        list_columns: Optional[List[str]] = None,
    ) -> DataTableRow:
        client = self._create_client(timeout=self.PDF_TIMEOUT)
        list_column_set = set(list_columns or [])

        try:
            aliases: Dict[str, str] = {f"col_{i}": col for i, col in enumerate(columns)}
            cols_str = "\n".join(
                f'- {alias}: "{col}"'
                + (
                    " [LIST: one keyed entry per instance found in the paper]"
                    if col in list_column_set
                    else ""
                )
                for alias, col in aliases.items()
            )
            prompt = EXTRACT_COLS_INSTRUCTION.format(
                cols_str=cols_str, n_cols=len(columns)
            )

            class ExtractedCell(BaseModel):
                value: str
                citations: List[ResponseCitation] = []

            class ExtractedEntry(BaseModel):
                key: str = Field(
                    description=(
                        "Label identifying which instance this entry belongs to "
                        "(model name, dataset, condition, etc.). Empty string if "
                        "the paper gives no such label."
                    )
                )
                value: str
                citations: List[ResponseCitation] = []

            field_definitions: Dict[str, Any] = {
                alias: (
                    (
                        List[ExtractedEntry],
                        Field(
                            description=(
                                "One keyed entry per instance found for column: "
                                f"{col!r}"
                            )
                        ),
                    )
                    if col in list_column_set
                    else (
                        ExtractedCell,
                        Field(description=f"Value and citations for column: {col!r}"),
                    )
                )
                for alias, col in aliases.items()
            }
            ValuesModel = create_model(
                "ValuesModel",
                __config__=ConfigDict(),
                **field_definitions,
            )

            values_instance = await self.generate_structured(
                prompt,
                schema=ValuesModel,
                model=self.default_model,
                file_path=file_path,
                client=client,
            )

            values_dict: Dict[str, DataTableCellValue] = {}
            for alias, col in aliases.items():
                extracted = getattr(values_instance, alias)
                if col in list_column_set:
                    entries = [
                        CellEntry(
                            value=e.value,
                            key=e.key.strip() or None,
                            citations=e.citations,
                        )
                        for e in extracted
                        if e.value.strip() and e.value.strip().upper() != "N/A"
                    ]
                    values_dict[col] = DataTableCellValue(
                        value=(
                            "; ".join(
                                f"{e.key}: {e.value}" if e.key else e.value
                                for e in entries
                            )
                            if entries
                            else "N/A"
                        ),
                        citations=[],
                        entries=entries,
                    )
                else:
                    values_dict[col] = DataTableCellValue(
                        value=extracted.value, citations=extracted.citations
                    )

            return DataTableRow(paper_id=paper_id, values=values_dict)
        except Exception as e:
            logger.error(f"Error extracting data table: {str(e)}", exc_info=True)
            raise ValueError(f"Failed to extract DT for paper {paper_id}: {str(e)}")
        finally:
            await self._close_client(client)


api_key = OPENAI_API_KEY
if not api_key:
    raise ValueError("OPENAI_API_KEY or OPENCODE_GO_API_KEY environment variable is not set")

llm_client = PaperOperations(api_key=api_key, default_model=DEFAULT_CHAT_MODEL)
fast_llm_client = PaperOperations(api_key=api_key, default_model=FAST_CHAT_MODEL)
