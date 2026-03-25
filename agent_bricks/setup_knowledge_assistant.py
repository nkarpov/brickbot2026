"""
Create the Knowledge Assistant for BrickBot using Databricks SDK.

This script programmatically creates a Knowledge Assistant with:
- Conference FAQ content
- Venue information
- Policies and guidelines

Run this once to set up the KA before creating the Supervisor Agent via UI.
"""

import os
import logging
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.knowledgeassistants import (
    KnowledgeAssistant,
    KnowledgeSource,
    FilesSpec,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
KA_NAME = "brickbot-knowledge-assistant"
KA_DESCRIPTION = """
Knowledge Assistant for DAIS 2026 conference information.
Answers questions about:
- Venue information (Moscone Center, rooms, maps, parking)
- Conference policies (code of conduct, badge policies, photo consent)
- General FAQ (WiFi, food, accessibility, registration)
- Speaker and session general information
"""

KA_INSTRUCTIONS = """
You are a helpful assistant for the Data + AI Summit 2026 (DAIS 2026).

Guidelines:
- Provide accurate information based on the conference documentation
- Include citations when referencing specific policies or guidelines
- For questions about specific sessions or schedules, direct users to use the session search
- Be concise but thorough
- If information is not in your knowledge base, say so clearly
"""

# Volume path for static content (must be created and populated separately)
CONTENT_VOLUME_PATH = "/Volumes/brickbot2026/content/faq"


def create_knowledge_assistant(w: WorkspaceClient) -> str:
    """Create the Knowledge Assistant and return its name."""
    
    logger.info("Creating Knowledge Assistant...")
    
    # Create the KA
    ka = KnowledgeAssistant(
        display_name=KA_NAME,
        description=KA_DESCRIPTION.strip(),
        instructions=KA_INSTRUCTIONS.strip(),
    )
    
    created = w.knowledge_assistants.create_knowledge_assistant(
        knowledge_assistant=ka
    )
    
    logger.info(f"Created Knowledge Assistant: {created.name}")
    logger.info(f"Endpoint name: {created.endpoint_name}")
    
    return created.name


def add_knowledge_source(w: WorkspaceClient, ka_name: str, volume_path: str) -> None:
    """Add a knowledge source (UC volume) to the Knowledge Assistant."""
    
    logger.info(f"Adding knowledge source from {volume_path}...")
    
    source = KnowledgeSource(
        display_name="Conference FAQ and Policies",
        description="Static content including FAQ, venue info, and policies for DAIS 2026",
        source_type="files",
        files=FilesSpec(path=volume_path),
    )
    
    created = w.knowledge_assistants.create_knowledge_source(
        parent=ka_name,
        knowledge_source=source,
    )
    
    logger.info(f"Added knowledge source: {created.name}")
    logger.info("Note: Sync will happen automatically. Check status in UI.")


def main():
    """Main entry point."""
    
    # Initialize client (uses environment or .databrickscfg)
    profile = os.environ.get("DATABRICKS_PROFILE", "brickbot")
    logger.info(f"Using Databricks profile: {profile}")
    
    w = WorkspaceClient(profile=profile)
    
    # Check if KA already exists
    try:
        existing = list(w.knowledge_assistants.list_knowledge_assistants())
        for ka in existing:
            if ka.display_name == KA_NAME:
                logger.info(f"Knowledge Assistant '{KA_NAME}' already exists: {ka.name}")
                logger.info(f"Endpoint: {ka.endpoint_name}")
                return ka.name
    except Exception as e:
        logger.warning(f"Could not list existing KAs: {e}")
    
    # Create new KA
    ka_name = create_knowledge_assistant(w)
    
    # Add knowledge source (if volume exists)
    try:
        add_knowledge_source(w, ka_name, CONTENT_VOLUME_PATH)
    except Exception as e:
        logger.warning(f"Could not add knowledge source: {e}")
        logger.info("You can add knowledge sources manually via the UI.")
        logger.info(f"Volume path to use: {CONTENT_VOLUME_PATH}")
    
    return ka_name


if __name__ == "__main__":
    main()
