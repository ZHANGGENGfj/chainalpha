from __future__ import annotations

from datetime import datetime
from pathlib import Path

from chainalpha.schemas import WeakSignal


class MarkdownReportRenderer:
    def render(self, signals: list[WeakSignal], title: str = "AI 产业链弱信号报告") -> str:
        signals = sorted(signals, key=lambda s: s.weak_signal_score, reverse=True)
        lines: list[str] = []
        lines.append(f"# {title}")
        lines.append("")
        lines.append(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        lines.append("## 1. 核心结论")
        lines.append("")
        if not signals:
            lines.append("本次扫描未发现足够明确的弱信号。建议扩大数据源或调整检索主题。")
            return "\n".join(lines)

        top = signals[:5]
        lines.append(f"本次共识别出 **{len(signals)}** 条候选产业链信号，其中前 **{len(top)}** 条值得优先人工复核。")
        lines.append("")
        lines.append("## 2. 弱信号排行榜")
        lines.append("")
        lines.append("| 排名 | 阶段 | 弱信号分 | 关注度 | 信号 | 产业链路径 |")
        lines.append("|---:|---|---:|---:|---|---|")
        for i, sig in enumerate(top, 1):
            path = " → ".join(sig.hypothesis.causal_path)
            lines.append(
                f"| {i} | {sig.stage} | {sig.weak_signal_score:.2f} | {sig.attention_score:.1f} | "
                f"{sig.title.replace('|', '/')} | {path.replace('|', '/')} |"
            )

        lines.append("")
        lines.append("## 3. 信号详情")
        lines.append("")
        for i, sig in enumerate(signals[:12], 1):
            lines.append(f"### {i}. {sig.title}")
            lines.append("")
            lines.append(f"- **阶段**：{sig.stage}")
            lines.append(f"- **弱信号分**：{sig.weak_signal_score:.2f}")
            lines.append(f"- **市场关注度**：{sig.attention_score:.1f}")
            lines.append(f"- **事件类型**：{sig.event.event_type}")
            lines.append(f"- **方向**：{sig.event.direction}")
            lines.append(f"- **产业链节点**：{', '.join(sig.event.supply_chain_nodes) or '未匹配'}")
            lines.append(f"- **因果链**：{' → '.join(sig.hypothesis.causal_path)}")
            lines.append(f"- **潜在瓶颈**：{', '.join(sig.hypothesis.bottleneck_nodes) or '暂无'}")
            lines.append("")
            lines.append("**推理假设：**")
            lines.append("")
            lines.append(sig.hypothesis.hypothesis)
            lines.append("")
            lines.append("**证据片段：**")
            for ev in sig.event.evidence[:3]:
                lines.append(f"> {ev}")
            lines.append("")
            lines.append("**评分原因：**")
            for r in sig.reasons:
                lines.append(f"- {r}")
            if sig.risks:
                lines.append("")
                lines.append("**风险提示：**")
                for r in sig.risks:
                    lines.append(f"- {r}")
            lines.append("")
            lines.append(f"来源文件：`{sig.event.source_path}`")
            lines.append("")

        lines.append("## 4. 下一步人工复核清单")
        lines.append("")
        lines.append("- 检查 S1/S2 信号是否有公告、财报、调研纪要交叉验证。")
        lines.append("- 对关注度低但因果链强的节点，补充公司映射和财务指标验证。")
        lines.append("- 对 S4 信号降权处理，避免追逐已经拥挤的热点。")
        lines.append("- 记录本次信号后续 1/3/6 个月验证结果，写回 Obsidian 复盘库。")
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("免责声明：本报告仅用于产业链研究和信息整理，不构成投资建议。")
        return "\n".join(lines)

    def save(self, markdown: str, out_dir: str | Path, filename: str | None = None) -> Path:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        if filename is None:
            filename = f"chainalpha_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        path = out / filename
        path.write_text(markdown, encoding="utf-8")
        return path
