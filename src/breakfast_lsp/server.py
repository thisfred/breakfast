import logging
import os
from itertools import groupby
from pathlib import Path

import breakfast
from lsprotocol.types import (
    INITIALIZE,
    TEXT_DOCUMENT_PREPARE_RENAME,
    TEXT_DOCUMENT_RENAME,
    CreateFile,
    DeleteFile,
    InitializeParams,
    MessageType,
    OptionalVersionedTextDocumentIdentifier,
    Position,
    PrepareRenameParams,
    PrepareRenameResult,
    Range,
    RenameFile,
    RenameParams,
    TextDocumentEdit,
    TextEdit,
    WorkspaceEdit,
)
from pygls.server import LanguageServer

from breakfast_lsp import __version__

logger = logging.getLogger(__name__)
BREAKFAST_DEBUG = bool(os.environ.get("BREAKFAST_DEBUG", False))
if BREAKFAST_DEBUG:
    log_file = Path(__file__).parent.parent / "breakfast-lsp.log"
    logging.basicConfig(filename=log_file, filemode="w", level=logging.INFO)

MAX_WORKERS = 2

LSP_SERVER = LanguageServer(
    name="breakfast",
    version=__version__,
    max_workers=MAX_WORKERS,
)


CLIENT_CAPABILITIES: dict[str, bool] = {
    TEXT_DOCUMENT_RENAME: True,
}


def find_identifier_range_at(
    server: LanguageServer, document_uri: str, position: Position
) -> Range | None:
    line = get_line(server, document_uri, position)
    if line is None:
        return None

    start = find_identifier_start(line, position)
    if start is None:
        return None

    end = find_identifier_end(line, position)

    return Range(
        start=Position(position.line, start),
        end=Position(position.line, end),
    )


def get_line(
    server: LanguageServer, document_uri: str, position: Position
) -> str | None:
    document = server.workspace.get_text_document(document_uri)
    try:
        return document.lines[position.line]
    except IndexError:
        return None


def find_identifier_start(line: str, position: Position) -> int | None:
    index = position.character
    if index < 0 or index >= len(line):
        return None

    char = line[index]

    if not char.isalnum() and char != "_":
        return None

    while index >= 0 and line[index].isalnum() or line[index] == "_":
        index -= 1

    if not line[index].isalnum() and line[index] != "_":
        index += 1

    if not line[index].isalpha():
        return None

    return index


def find_identifier_end(line: str, position: Position) -> int:
    end = position.character

    while end < len(line) and line[end].isalnum() or line[end] == "_":
        end += 1

    return end


@LSP_SERVER.feature(INITIALIZE)
def initialize(server: LanguageServer, params: InitializeParams) -> None:
    logger.info(f"{server.workspace.root_uri=}")
    logger.info(f"{server.workspace.text_documents=}")


@LSP_SERVER.feature(TEXT_DOCUMENT_PREPARE_RENAME)
async def prepare_rename(
    server: LanguageServer, params: PrepareRenameParams
) -> PrepareRenameResult | None:
    return find_identifier_range_at(
        server, document_uri=params.text_document.uri, position=params.position
    )


@LSP_SERVER.feature(TEXT_DOCUMENT_RENAME)
async def rename(server: LanguageServer, params: RenameParams) -> WorkspaceEdit | None:
    document = server.workspace.get_text_document(params.text_document.uri)
    source_lines = tuple(document.source.split("\n"))
    line = source_lines[params.position.line]

    start = find_identifier_start(line, params.position)
    if start is None:
        return None

    source = breakfast.Source(
        source_lines, filename=params.text_document.uri[len("file://") :]
    )
    project_root = server.workspace.root_uri
    application = breakfast.Application(source=source, root=project_root)
    position = source.position(row=params.position.line, column=start)
    occurrences = application.get_occurrences(position)
    if not occurrences:
        return None

    logger.info(f"found {len(occurrences)} occurrences to rename.")
    old_identifier = source.get_name_at(position)
    document_changes: list[TextDocumentEdit | CreateFile | RenameFile | DeleteFile] = []
    client_documents = server.workspace.text_documents
    for source, source_occurences in groupby(occurrences, lambda o: o.source):
        document_uri = f"file://{source.filename}"
        logger.info(f"{document_uri=}")
        version = (
            versioned.version
            if (versioned := client_documents.get(document_uri))
            else None
        )
        document_changes.append(
            TextDocumentEdit(
                text_document=OptionalVersionedTextDocumentIdentifier(
                    uri=document_uri, version=version
                ),
                edits=[
                    TextEdit(
                        range=Range(
                            start=Position(line=o.row, character=o.column),
                            end=Position(
                                line=o.row, character=o.column + len(old_identifier)
                            ),
                        ),
                        new_text=params.new_name,
                    )
                    for o in source_occurences
                ],
            )
        )

    return WorkspaceEdit(
        document_changes=document_changes,
    )


def show_message(message: str) -> None:
    LSP_SERVER.show_message_log(message, MessageType.Log)


def start() -> None:
    LSP_SERVER.start_io()


if __name__ == "__main__":
    start()
