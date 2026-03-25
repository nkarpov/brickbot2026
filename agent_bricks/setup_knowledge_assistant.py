"""
Create the Knowledge Assistant for BrickBot using Databricks SDK.

This script programmatically creates a Knowledge Assistant with:
- Conference FAQ content
- Venue information
- Policies and guidelines

Run this once to set up the KA before creating the Supervisor Agent via UI.
"""

import logging
import os
import re

from databricks.sdk import WorkspaceClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from databricks.sdk.service.knowledgeassistants import (
        FilesSpec,
        KnowledgeAssistant,
        KnowledgeSource,
    )
except ImportError:  # pragma: no cover - depends on deploy image SDK version
    FilesSpec = None
    KnowledgeAssistant = None
    KnowledgeSource = None

def default_ka_name() -> str:
    configured = os.environ.get("KA_NAME")
    if configured:
        return configured

    suffix = (
        os.environ.get("LAKEBASE_AUTOSCALING_BRANCH")
        or os.environ.get("BRICKBOT_BACKEND_VARIANT")
        or "agent-bricks"
    )
    suffix = re.sub(r"[^a-z0-9-]+", "-", suffix.lower()).strip("-")
    if not suffix:
        suffix = "agent-bricks"
    return f"brickbot-knowledge-assistant-{suffix}"


# Configuration
KA_NAME = default_ka_name()
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
CONTENT_VOLUME_PATH = os.environ.get(
    "KA_CONTENT_VOLUME_PATH",
    "/Volumes/brickbot2026/content/faq",
)
AUTO_SYNC_SOURCES = os.environ.get("KA_AUTO_SYNC_SOURCES", "true").lower() in {
    "1",
    "true",
    "yes",
}


def find_knowledge_assistant(w: WorkspaceClient):
    """Return the visible Knowledge Assistant matching KA_NAME, if any."""
    for ka in w.knowledge_assistants.list_knowledge_assistants():
        if ka.display_name == KA_NAME:
            return ka
    return None


def create_knowledge_assistant(w: WorkspaceClient):
    """Create the Knowledge Assistant and return the created object."""
    if KnowledgeAssistant is None:
        raise RuntimeError(
            "This environment's databricks-sdk does not include Knowledge Assistants. "
            "Upgrade databricks-sdk in deployment or create the Knowledge Assistant manually in the UI."
        )

    logger.info("Creating Knowledge Assistant '%s'...", KA_NAME)

    ka = KnowledgeAssistant(
        display_name=KA_NAME,
        description=KA_DESCRIPTION.strip(),
        instructions=KA_INSTRUCTIONS.strip(),
    )

    try:
        created = w.knowledge_assistants.create_knowledge_assistant(
            knowledge_assistant=ka
        )
    except Exception as exc:
        if "ALREADY_EXISTS" not in str(exc):
            raise
        existing = find_knowledge_assistant(w)
        if existing is None:
            raise RuntimeError(
                f"Knowledge Assistant '{KA_NAME}' already exists but is not visible to "
                "the current identity. Use a unique KA_NAME for this app deployment."
            ) from exc
        logger.info(
            "Knowledge Assistant '%s' already exists and is accessible: %s",
            KA_NAME,
            existing.name,
        )
        return existing

    logger.info(f"Created Knowledge Assistant: {created.name}")
    logger.info(f"Endpoint name: {created.endpoint_name}")
    return created


def add_knowledge_source(w: WorkspaceClient, ka_name: str, volume_path: str) -> None:
    """Add a knowledge source (UC volume) to the Knowledge Assistant."""
    if KnowledgeSource is None or FilesSpec is None:
        raise RuntimeError(
            "Knowledge Assistant SDK types are unavailable in this environment."
        )

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
    if AUTO_SYNC_SOURCES:
        sync_knowledge_sources(w, ka_name)


def sync_knowledge_sources(w: WorkspaceClient, ka_name: str) -> None:
    logger.info("Syncing knowledge sources...")
    w.knowledge_assistants.sync_knowledge_sources(name=ka_name)
    logger.info("Knowledge source sync requested.")


def get_endpoint_name(ka: object) -> str | None:
    return getattr(ka, "endpoint_name", None)


def get_resource_name(ka: object) -> str | None:
    return getattr(ka, "name", None)


def get_display_name(ka: object) -> str | None:
    return getattr(ka, "display_name", None)


def get_id(ka: object) -> str | None:
    return getattr(ka, "id", None)


def get_ka_name_for_calls(ka: object) -> str:
    return get_resource_name(ka) or f"knowledge-assistants/{get_id(ka)}"


def ensure_source_synced(w: WorkspaceClient, ka_name: str) -> None:
    if AUTO_SYNC_SOURCES:
        sync_knowledge_sources(w, ka_name)


def ensure_knowledge_source(w: WorkspaceClient, ka_name: str, volume_path: str) -> None:
    """Ensure the expected files source exists for the Knowledge Assistant."""
    try:
        existing_sources = list(w.knowledge_assistants.list_knowledge_sources(parent=ka_name))
    except Exception as exc:
        logger.warning(f"Could not list knowledge sources: {exc}")
        existing_sources = []

    for source in existing_sources:
        if source.source_type == "files" and source.files and source.files.path == volume_path:
            logger.info(
                "Knowledge source for %s already exists: %s",
                volume_path,
                source.name,
            )
            ensure_source_synced(w, ka_name)
            return

    add_knowledge_source(w, ka_name, volume_path)


def get_workspace_client() -> WorkspaceClient:
    profile = os.environ.get("DATABRICKS_PROFILE")
    if profile:
        logger.info(f"Using Databricks profile: {profile}")
        return WorkspaceClient(profile=profile)
    logger.info("Using ambient Databricks auth from environment.")
    return WorkspaceClient()


def main():
    """Main entry point."""
    w = get_workspace_client()

    if KnowledgeAssistant is None:
        logger.error(
            "databricks-sdk in this environment does not expose Knowledge Assistants."
        )
        logger.error(
            "Next step: update the deployment image to use a newer databricks-sdk, "
            "or create the Knowledge Assistant manually in the Databricks UI."
        )
        return None
    try:
        ka = find_knowledge_assistant(w)
        if ka is not None:
            logger.info(
                "Knowledge Assistant '%s' already exists: %s",
                get_display_name(ka),
                get_resource_name(ka),
            )
            logger.info("Endpoint: %s", get_endpoint_name(ka))
            ensure_knowledge_source(w, get_ka_name_for_calls(ka), CONTENT_VOLUME_PATH)
            return get_resource_name(ka)
    except Exception as e:
        logger.warning(f"Could not list existing KAs: {e}")

    created = create_knowledge_assistant(w)
    ka_name = get_ka_name_for_calls(created)
    logger.info("Using Knowledge Assistant resource: %s", ka_name)
    logger.info("Endpoint: %s", get_endpoint_name(created))

    try:
        ensure_knowledge_source(w, ka_name, CONTENT_VOLUME_PATH)
    except Exception as e:
        logger.warning(f"Could not add knowledge source: {e}")
        logger.info("You can add knowledge sources manually via the UI.")
        logger.info(f"Volume path to use: {CONTENT_VOLUME_PATH}")

    return ka_name


if __name__ == "__main__":
    main()
