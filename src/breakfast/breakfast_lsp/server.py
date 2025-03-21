import logging
import os
from collections.abc import Iterable
from itertools import groupby
from pathlib import Path

from lsprotocol.types import (
    INITIALIZE,
    TEXT_DOCUMENT_CODE_ACTION,
    TEXT_DOCUMENT_PREPARE_RENAME,
    TEXT_DOCUMENT_RENAME,
    AnnotatedTextEdit,
    CodeAction,
    CodeActionKind,
    CodeActionOptions,
    CodeActionParams,
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

from breakfast import __version__
from breakfast.project import Project
from breakfast.refactoring import CodeSelection, Refactoring
from breakfast.source import Source, TextRange
from breakfast.types import Edit

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

    logger.debug(f"found range: {start=}, {end=}")
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


def is_valid_identifier_character(character: str) -> bool:
    return character.isalnum() or character == "_"


def is_valid_identifier_start(character: str) -> bool:
    return character.isalpha() or character == "_"


def find_identifier_start(line: str, position: Position) -> int | None:
    start = position.character
    if start < 0 or start >= len(line):
        logger.debug("Invalid position.")
        return None

    if not is_valid_identifier_character(line[start]):
        logger.debug("Cursor not at a name.")
        return None

    while start >= 0 and is_valid_identifier_character(line[start]):
        start -= 1

    if not is_valid_identifier_start(line[start]):
        start += 1

    if not is_valid_identifier_start(line[start]):
        return None

    return start


def find_identifier_end(line: str, position: Position) -> int:
    end = position.character

    while end < len(line) and is_valid_identifier_character(line[end]):
        end += 1

    return end


@LSP_SERVER.feature(INITIALIZE)
def initialize(server: LanguageServer, params: InitializeParams) -> None:
    logger.debug(f"{server.workspace.root_uri=}")
    logger.debug(f"{server.workspace.text_documents=}")


@LSP_SERVER.feature(TEXT_DOCUMENT_PREPARE_RENAME)
async def prepare_rename(
    server: LanguageServer, params: PrepareRenameParams
) -> PrepareRenameResult | None:
    return find_identifier_range_at(
        server, document_uri=params.text_document.uri, position=params.position
    )


def get_source(uri: str, project_root: str, lines: Iterable[str]) -> Source:
    return Source(
        input_lines=tuple(line for line in lines),
        path=uri[len("file://") :],
        project_root=project_root,
    )


@LSP_SERVER.feature(TEXT_DOCUMENT_RENAME)
async def rename(
    server: LanguageServer, params: RenameParams
) -> WorkspaceEdit | None:
    document = server.workspace.get_text_document(params.text_document.uri)
    source_lines = tuple(document.source.split("\n"))
    line = source_lines[params.position.line]

    start = find_identifier_start(line, params.position)
    if start is None:
        return None

    project_root = server.workspace.root_uri[len("file://") :]
    source = get_source(
        uri=params.text_document.uri,
        project_root=project_root,
        lines=source_lines,
    )
    project = Project(source=source, root=project_root)
    position = source.position(row=params.position.line, column=start)
    occurrences = project.get_occurrences(position)
    if not occurrences:
        return None

    logger.debug(f"found {len(occurrences)} occurrences to rename.")
    old_identifier = source.get_name_at(position)
    if old_identifier is None:
        return None
    document_changes: list[
        TextDocumentEdit | CreateFile | RenameFile | DeleteFile
    ] = []
    client_documents = server.workspace.text_documents
    for source, source_occurences in groupby(occurrences, lambda o: o.source):
        document_uri = f"file://{source.path}"
        logger.debug(f"{document_uri=}")
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
                                line=o.row,
                                character=o.column + len(old_identifier),
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


def edits_to_text_edits(
    edits: Iterable[Edit],
) -> list[TextEdit | AnnotatedTextEdit]:
    return [
        TextEdit(
            range=Range(
                start=Position(
                    line=edit.start.row, character=edit.start.column
                ),
                end=Position(line=edit.end.row, character=edit.end.column),
            ),
            new_text=edit.text,
        )
        for edit in edits
    ]


@LSP_SERVER.feature(
    TEXT_DOCUMENT_CODE_ACTION,
    CodeActionOptions(
        code_action_kinds=[
            CodeActionKind.RefactorExtract,
            CodeActionKind.Refactor,
        ],
        resolve_provider=True,
    ),
)
async def code_action(
    server: LanguageServer, params: CodeActionParams
) -> list[CodeAction] | None:
    actions: list[CodeAction] = []
    if params.range:
        logger.debug(f"{params.range=}")
        document_uri = params.text_document.uri
        document = server.workspace.get_text_document(document_uri)
        source_lines = tuple(document.source.split("\n"))
        project_root = server.workspace.root_uri[len("file://") :]
        client_documents = server.workspace.text_documents
        version = (
            versioned.version
            if (versioned := client_documents.get(document_uri))
            else None
        )
        source = get_source(
            uri=document_uri, project_root=project_root, lines=source_lines
        )
        extraction_range = params.range
        start = source.position(
            row=extraction_range.start.line,
            column=extraction_range.start.character,
        )
        end = source.position(
            row=extraction_range.end.line,
            column=max(extraction_range.end.character, 0),
        )
        selection = CodeSelection(text_range=TextRange(start, end))

        for new_refactoring in selection.refactorings:
            actions.append(
                CodeAction(
                    title=f"breakfast: {new_refactoring.name}",
                    kind=CodeActionKind.RefactorExtract
                    if "extract" in new_refactoring.name
                    else CodeActionKind.Refactor,
                    data=document_uri,
                    edit=await get_edits(
                        new_refactoring(selection), document_uri, version
                    ),
                    diagnostics=[],
                )
            )

    return actions


async def get_edits(
    refactoring: Refactoring, document_uri: str, version: None
) -> WorkspaceEdit:
    text_edits: list[TextEdit | AnnotatedTextEdit] = edits_to_text_edits(
        refactoring.edits
    )
    document_changes: list[
        TextDocumentEdit | CreateFile | RenameFile | DeleteFile
    ] = [
        TextDocumentEdit(
            text_document=OptionalVersionedTextDocumentIdentifier(
                uri=document_uri, version=version
            ),
            edits=text_edits,
        )
    ]
    return WorkspaceEdit(document_changes=document_changes)


# @LSP_SERVER.command("breakfast.slideStatementsDown")
# async def slide_statements_down(
#     server: LanguageServer, arguments: Sequence[Mapping[str, Any]]
# ) -> None:
#     document_uri = arguments[0]["uri"]
#     line = arguments[0]["line"] - 1
#     document = server.workspace.get_text_document(document_uri)

#     source_lines = tuple(document.source.split("\n"))
#     project_root = server.workspace.root_uri[len("file://") :]
#     source = get_source(
#         uri=document_uri, project_root=project_root, lines=source_lines
#     )
#     start = source.position(row=line, column=0)
#     end = source.position(row=line, column=0)
#     selection = CodeSelection(text_range=TextRange(start, end))

#     client_documents = server.workspace.text_documents
#     version = (
#         versioned.version
#         if (versioned := client_documents.get(document_uri))
#         else None
#     )
#     workspace_edit = await _slide_statements_down(
#         selection, document_uri, version
#     )
#     if workspace_edit is None:
#         return
#     server.apply_edit(workspace_edit, "breakfast: slide statement")


# @LSP_SERVER.command("breakfast.slideStatementsUp")
# async def slide_statements_up(
#     server: LanguageServer, arguments: Sequence[Mapping[str, Any]]
# ) -> None:
#     document_uri = arguments[0]["uri"]
#     line = arguments[0]["line"] - 1
#     document = server.workspace.get_text_document(document_uri)

#     source_lines = tuple(document.source.split("\n"))
#     project_root = server.workspace.root_uri[len("file://") :]
#     source = get_source(
#         uri=document_uri, project_root=project_root, lines=source_lines
#     )
#     start = source.position(row=line, column=0)
#     end = source.position(row=line, column=0)
#     selection = CodeSelection(text_range=TextRange(start, end))

#     client_documents = server.workspace.text_documents
#     version = (
#         versioned.version
#         if (versioned := client_documents.get(document_uri))
#         else None
#     )
#     workspace_edit = await _slide_statements_up(
#         selection, document_uri, version
#     )
#     if workspace_edit is None:
#         return
#     server.apply_edit(workspace_edit, "breakfast: slide statement")


def show_message(message: str) -> None:
    LSP_SERVER.show_message_log(message, MessageType.Log)


def start() -> None:
    LSP_SERVER.start_io()


if __name__ == "__main__":
    start()
