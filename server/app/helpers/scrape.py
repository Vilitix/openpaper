import os

from firecrawl import FirecrawlApp

FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
# Optional: URL scraping is one tool the assistant can use, not something that
# should prevent a self-hosted deployment from booting.
firecrawl_app = FirecrawlApp(api_key=FIRECRAWL_API_KEY) if FIRECRAWL_API_KEY else None


def scrape_web_page(url: str) -> str:
    """
    Scrape the content of a web page using Firecrawl.

    Args:
        url (str): The URL of the web page to scrape.

    Returns:
        str: The scraped content of the web page.
    """
    try:
        if firecrawl_app is None:
            raise Exception(
                "Firecrawl is not configured; set FIRECRAWL_API_KEY to enable "
                "web page scraping"
            )
        # firecrawl-py 4.x: scrape() returns a Document (with .markdown) on
        # success and raises on failure. There is no .error attribute.
        document = firecrawl_app.scrape(url, formats=["markdown"])
        if document.markdown:
            return document.markdown
        raise Exception(f"Failed to scrape {url}: no markdown content returned")
    except Exception as e:
        # Scrape failures are expected (anti-bot walls, dead links) and the
        # caller logs them at warning; don't also log at error here.
        raise Exception(f"Error scraping {url}: {str(e)}")
