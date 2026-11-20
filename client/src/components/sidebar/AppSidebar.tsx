"use client"

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar, SidebarContent } from "@/components/ui/sidebar";
import { fetchFromApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useIsDarkMode } from "@/hooks/useDarkMode";
import { useIsMobile } from "@/hooks/use-mobile";
import { useActivePapers } from "@/hooks/useActivePapers";
import { Conversation, Project } from "@/lib/schema";
import { SidebarNav } from "./SidebarNav";
import { AppSidebarFooter } from "./SidebarFooter";

export function AppSidebar() {
    const router = useRouter();
    const { user, logout } = useAuth();
    const { papers: allPapers } = useActivePapers(!!user);
    const [projects, setProjects] = useState<Project[]>([]);
    const [everythingConversations, setEverythingConversations] = useState<Conversation[]>([]);
    const { darkMode, toggleDarkMode } = useIsDarkMode();
    const isMobile = useIsMobile();

    useEffect(() => {
        if (!user) {
            setEverythingConversations([]);
            setProjects([]);
            return;
        }

        const fetchData = async () => {
            try {
                const [conversationsResponse, projectsResponse] = await Promise.all([
                    fetchFromApi("/api/conversation/everything"),
                    fetchFromApi("/api/projects"),
                ]);

                setEverythingConversations(conversationsResponse || []);
                setProjects(projectsResponse || []);
            } catch (error) {
                console.error("Error fetching sidebar data:", error);
                setEverythingConversations([]);
                setProjects([]);
            }
        };

        fetchData();
    }, [user]);

    const handleLogout = async () => {
        await logout();
        router.push('/login');
    }

    return (
        <Sidebar variant="floating">
            <SidebarContent>
                <SidebarNav
                    user={user}
                    papers={allPapers}
                    conversations={everythingConversations}
                    projects={projects}
                />
            </SidebarContent>
            <AppSidebarFooter
                user={user}
                isMobile={isMobile}
                darkMode={darkMode}
                onToggleDarkMode={toggleDarkMode}
                onLogout={handleLogout}
            />
        </Sidebar>
    )
}
