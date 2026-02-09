# noqa: INP001
"""Development server for TAIC Smart Tools.

Runs both the FastAPI/Gradio app and auto-rebuilds documentation when files change.

Usage:
    uv run working_files/dev.py
"""

import logging
import shutil
import subprocess  # noqa: S404
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logging.basicConfig(format="%(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


def resolve_executable(name: str) -> str:
    """Resolve full path to an executable or raise an error.

    Args:
        name: Executable name to resolve.

    Returns:
        The resolved full path to the executable.

    Raises:
        FileNotFoundError: If the executable cannot be found on PATH.
    """
    path = shutil.which(name)
    if not path:
        msg = f"Executable not found: {name}"
        raise FileNotFoundError(msg)
    return path


class DocsRebuilder(FileSystemEventHandler):
    """Handles file system events and triggers documentation rebuilds."""

    def __init__(self):
        """Initialize the docs rebuilder with debouncing settings."""
        self.last_rebuild = 0
        self.debounce_seconds = 1.5
        self.pending_rebuild = False

    @staticmethod
    def should_rebuild(event):
        """Check if the event should trigger a rebuild.

        Args:
            event: The file system event to check.

        Returns:
            True if the event should trigger a rebuild, False otherwise.
        """
        if event.is_directory:
            return False

        path = Path(event.src_path)

        # Watch for markdown files in docs/
        if path.suffix == ".md" and "docs" in path.parts:
            return True

        # Watch for Python files in backend/
        if path.suffix == ".py" and "backend" in path.parts:
            return True

        # Watch for specific files in the root
        return path.name in {"mkdocs.yml", "app.py"}

    def on_modified(self, event):
        """Handle file modification events."""
        if self.should_rebuild(event):
            self.schedule_rebuild(event.src_path)

    def on_created(self, event):
        """Handle file creation events."""
        if self.should_rebuild(event):
            self.schedule_rebuild(event.src_path)

    def schedule_rebuild(self, path):
        """Schedule a rebuild with debouncing."""
        current_time = time.time()
        self.pending_rebuild = True

        if current_time - self.last_rebuild < self.debounce_seconds:
            return

        self.rebuild_docs(path)

    def rebuild_docs(self, changed_file):
        """Execute mkdocs build command."""
        self.last_rebuild = time.time()
        self.pending_rebuild = False

        logger.debug("\n🔄 Change detected: %s", Path(changed_file).name)
        logger.info("📚 Rebuilding docs...")

        try:
            result = subprocess.run(  # noqa: S603
                [
                    resolve_executable("uv"),
                    "run",
                    resolve_executable("mkdocs"),
                    "build",
                    "--quiet",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                logger.debug("✅ Done!")
            else:
                logger.error("❌ Failed!")
                if result.stderr:
                    logger.error("   %s", result.stderr.strip())
        except Exception:
            logger.exception("❌ Error failed to rebuild docs")


def start_docs_watcher():
    """Start watching files and rebuilding docs.

    Returns:
        A tuple containing the observer and event handler instances.
    """
    # Initial build
    logger.info("📚 Building documentation...")
    try:
        subprocess.run(  # noqa: S603
            [
                resolve_executable("uv"),
                "run",
                resolve_executable("mkdocs"),
                "build",
                "--quiet",
            ],
            check=True,
            timeout=30,
        )
        logger.info("✅")
    except Exception:
        logger.exception("❌ Failed Build of docs the first time")

    event_handler = DocsRebuilder()
    observer = Observer()

    # Watch docs and backend directories
    observer.schedule(event_handler, "docs", recursive=True)
    observer.schedule(event_handler, "backend", recursive=True)
    observer.schedule(event_handler, ".", recursive=False)

    observer.start()
    return observer, event_handler


def main():
    """Start all development services."""
    logger.info("🚀 TAIC Smart Tools - Development Server")
    logger.info("=" * 50)
    logger.info("")
    logger.info("Services:")
    logger.info("  📱 Gradio App:  http://localhost:7860")
    logger.info("  📚 Docs:        http://localhost:7860/documentation")
    logger.info("")
    logger.info("Auto-reload enabled for:")
    logger.info("  • Python files (app.py, backend/)")
    logger.info("  • Documentation (docs/, mkdocs.yml)")
    logger.info("")
    logger.info("Press Ctrl+C to stop all services")
    logger.info("=" * 50)
    logger.info("")

    # Start docs watcher
    observer, event_handler = start_docs_watcher()

    # Give a moment for initial build
    time.sleep(1)

    # Start FastAPI/Gradio app
    logger.info("📱 Starting Gradio app...")

    app_process = subprocess.Popen(  # noqa: S603
        [
            resolve_executable("uv"),
            "run",
            resolve_executable("uvicorn"),
            "app:app",
            "--host",
            "localhost",
            "--port",
            "7860",
            "--reload",
            "--timeout-graceful-shutdown",
            "2",
        ],
    )

    try:
        # Watch for pending doc rebuilds
        while True:
            time.sleep(1)

            # Check if we have a pending rebuild after debounce
            if event_handler.pending_rebuild:
                current_time = time.time()
                if (
                    current_time - event_handler.last_rebuild
                    >= event_handler.debounce_seconds
                ):
                    event_handler.rebuild_docs("(batched changes)")

            # Check if app process died
            if app_process.poll() is not None:
                logger.error("\n❌ App process ended unexpectedly")
                break

    except KeyboardInterrupt:
        logger.info("\n\n🛑 Shutting down...")
    finally:
        # Cleanup
        observer.stop()
        observer.join()

        if app_process.poll() is None:
            app_process.terminate()
            try:
                app_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                app_process.kill()

        logger.info("✅ All services stopped")


if __name__ == "__main__":
    main()
