"""Artifact preparation keeps source checks and forwards design requests to Studio."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import artifacts, lightrag_engine, multihop, spreadsheet_query


def test_source_revision_is_checked_and_presentation_prompt_is_separate(monkeypatch):
    source = "Hybrid retrieval combines vector similarity with graph relationships."
    response = {"status": "success", "data": {"chunks": [{"content": source}]},
                "llm_response": {"content": "# Unrelated\nMartian glaciers contain platinum deposits and alien fossils."}}
    rag = SimpleNamespace(aquery_llm=AsyncMock(return_value=response))
    monkeypatch.setattr(lightrag_engine, "get_rag", AsyncMock(return_value=rag))
    monkeypatch.setattr(spreadsheet_query, "relevant_tables", AsyncMock(return_value=[]))
    monkeypatch.setattr(multihop, "gather", AsyncMock(return_value=[]))
    revision = AsyncMock(return_value="# Retrieval\n## Architecture\n" + source)
    monkeypatch.setattr(lightrag_engine, "query_llm_func", revision)
    monkeypatch.setenv("ARTIFACT_MIN_GROUNDED_RATIO", "0.6")

    document, _ = asyncio.run(artifacts.prepare("Create a PDF about retrieval", [], "pdf"))
    assert document.sections[0].blocks[0].text == source
    assert "Deck Studio supplies" not in rag.aquery_llm.call_args.kwargs["param"].user_prompt
    assert source in revision.call_args.args[0]
    asyncio.run(artifacts.prepare("Create slides about retrieval", [], "pptx"))
    assert "Deck Studio supplies" in rag.aquery_llm.call_args.kwargs["param"].user_prompt

    revision.return_value = response["llm_response"]["content"]
    with pytest.raises(ValueError, match="supported by your sources"):
        asyncio.run(artifacts.prepare("Create a PDF about retrieval", []))


def test_renderer_gets_original_design_request(monkeypatch, tmp_path):
    from app.core import auth
    from noderels_artifacts import from_markdown

    doc = from_markdown("# Retrieval\n## Architecture\nVector similarity and graphs.", "Brief", [])
    prepare = AsyncMock(return_value=(doc, []))
    remote = AsyncMock(return_value={"id": "remote"})
    monkeypatch.setattr(artifacts, "prepare", prepare)
    monkeypatch.setattr(artifacts, "store", lambda: SimpleNamespace(folder=lambda _: tmp_path))
    monkeypatch.setattr(auth, "ensure_stores", AsyncMock())
    monkeypatch.setattr(artifacts.agent_client, "call", remote)
    request = "Make an academic presentation with images"
    result = asyncio.run(artifacts.execute({"id": "local", "owner": "alice", "payload": {
        "message": request, "history": [], "format": "pptx"}}))
    assert result["remote_id"] == "remote"
    assert remote.call_args.args[1]["request"] == request
    assert remote.call_args.args[1]["document"] == doc.model_dump()
    prepare.assert_awaited_once_with(request, [], "pptx")
