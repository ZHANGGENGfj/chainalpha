from __future__ import annotations

from pathlib import Path
from typing import Any

from chainalpha.connectors.base import FetchRequest
from chainalpha.connectors.document_adapter import source_documents_to_chunks
from chainalpha.connectors.registry import SourceManager
from chainalpha.memory.obsidian_loader import ObsidianLoader
from chainalpha.memory.index import HybridIndex
from chainalpha.ontology.industry_graph import IndustryGraph
from chainalpha.nlp.event_extractor import RuleEventExtractor
from chainalpha.reasoning.constraint_detector import ConstraintReasoner
from chainalpha.scoring.weak_signal_score import WeakSignalScorer
from chainalpha.reports.markdown_renderer import MarkdownReportRenderer
from chainalpha.schemas import Chunk, SourceDocument, WeakSignal
from chainalpha.storage.local_store import LocalResearchStore


DEFAULT_QUERY = (
    "AI产业链 AI CapEx 数据中心 GPU HBM CoWoS 先进封装 AI服务器 "
    "光模块 800G 1.6T 硅光 CPO LPO 电力 液冷 企业AI Agent RAG"
)


def analyze_chunks(
    chunks: list[Chunk],
    query: str = DEFAULT_QUERY,
    top_k_chunks: int = 80,
    out_dir: str | Path | None = None,
    store: LocalResearchStore | None = None,
) -> tuple[list[WeakSignal], str, Path | None]:
    if not chunks:
        renderer = MarkdownReportRenderer()
        markdown = "# ChainAlpha 弱信号报告\n\n没有可分析的文档。"
        saved_path = renderer.save(markdown, out_dir) if out_dir else None
        return [], markdown, saved_path

    index = HybridIndex(chunks)
    selected = index.search(query, top_k=top_k_chunks)

    graph = IndustryGraph.load_ai_graph()
    extractor = RuleEventExtractor(graph)
    events = extractor.extract(selected)

    if store is not None:
        store.save_events(events)

    reasoner = ConstraintReasoner(graph)
    scorer = WeakSignalScorer()

    signals: list[WeakSignal] = []

    for event in events:
        hyp = reasoner.build_hypothesis(event)
        signals.append(scorer.score(event, hyp))

    signals = dedup_signals(signals)

    if store is not None:
        store.save_signals(signals)

    renderer = MarkdownReportRenderer()
    markdown = renderer.render(signals)
    saved_path = renderer.save(markdown, out_dir) if out_dir else None

    return signals, markdown, saved_path


def dedup_signals(signals: list[WeakSignal]) -> list[WeakSignal]:
    """
    对弱信号做内存级去重。

    保留同一 source + event_type + causal_path 下分数最高的信号。
    """

    dedup: dict[tuple[Any, ...], WeakSignal] = {}

    for sig in signals:
        event = sig.event
        hyp = sig.hypothesis

        source_path = getattr(event, "source_path", "")
        event_type = getattr(event, "event_type", "")
        causal_path = tuple(getattr(hyp, "causal_path", []) or [])

        key = (
            source_path,
            event_type,
            causal_path,
        )

        old = dedup.get(key)

        if old is None:
            dedup[key] = sig
            continue

        if sig.weak_signal_score > old.weak_signal_score:
            dedup[key] = sig

    return sorted(
        dedup.values(),
        key=lambda s: s.weak_signal_score,
        reverse=True,
    )


def run_scan(
    vault_path: str | Path,
    query: str = DEFAULT_QUERY,
    top_k_chunks: int = 80,
    out_dir: str | Path | None = None,
    data_dir: str | Path = "data",
    persist: bool = True,
) -> tuple[list[WeakSignal], str, Path | None]:
    loader = ObsidianLoader(vault_path)
    chunks = loader.load_chunks()

    store = LocalResearchStore(data_dir) if persist else None

    if store is not None:
        chunks, _ = store.save_processed_chunks(chunks)

    return analyze_chunks(
        chunks,
        query=query,
        top_k_chunks=top_k_chunks,
        out_dir=out_dir,
        store=store,
    )


def run_scan_from_documents(
    documents: list[SourceDocument],
    query: str = DEFAULT_QUERY,
    top_k_chunks: int = 80,
    out_dir: str | Path | None = None,
    data_dir: str | Path = "data",
    persist: bool = True,
) -> tuple[list[WeakSignal], str, Path | None]:
    store = LocalResearchStore(data_dir) if persist else None

    if store is not None:
        documents, _ = store.save_raw_documents(
            documents,
            source_name="manual_documents",
        )

    chunks = source_documents_to_chunks(documents)

    if store is not None:
        chunks, _ = store.save_processed_chunks(chunks)

    return analyze_chunks(
        chunks,
        query=query,
        top_k_chunks=top_k_chunks,
        out_dir=out_dir,
        store=store,
    )


def run_scan_from_sources(
    sources_config: str | Path,
    query: str = DEFAULT_QUERY,
    keywords: list[str] | None = None,
    top_k_chunks: int = 80,
    out_dir: str | Path | None = None,
    limit: int | None = 500,
    data_dir: str | Path = "data",
    persist: bool = True,
) -> tuple[list[WeakSignal], str, Path | None, list[str]]:
    manager = SourceManager.from_yaml(sources_config)

    request = FetchRequest(
        query=query,
        keywords=keywords or [],
        limit=limit,
    )

    documents, results = manager.fetch_all(request)

    errors = [*manager.errors]

    for result in results:
        errors.extend(
            [
                f"{result.source_name}: {err}"
                for err in result.errors
            ]
        )

    store = LocalResearchStore(data_dir) if persist else None

    new_documents: list[SourceDocument] = []

    if store is not None:
        for result in results:
            source_docs = result.documents or []

            saved_docs, save_info = store.save_raw_documents(
                source_docs,
                source_name=result.source_name,
            )

            new_documents.extend(saved_docs)

            errors.append(
                "[storage] "
                f"{result.source_name}: "
                f"saved={save_info['saved']} "
                f"skipped={save_info['skipped']} "
                f"path={save_info['path']}"
            )
    else:
        new_documents = documents

    chunks = source_documents_to_chunks(new_documents)

    if store is not None:
        chunks, chunk_info = store.save_processed_chunks(chunks)
        errors.append(
            "[storage] "
            f"processed_chunks: "
            f"saved={chunk_info['saved']} "
            f"skipped={chunk_info['skipped']} "
            f"path={chunk_info['path']}"
        )

    signals, markdown, saved = analyze_chunks(
        chunks,
        query=query,
        top_k_chunks=top_k_chunks,
        out_dir=out_dir,
        store=store,
    )

    return signals, markdown, saved, errors