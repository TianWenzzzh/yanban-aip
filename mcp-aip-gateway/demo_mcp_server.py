"""模拟科研工具 MCP Server（研究用）。

两个工具模拟"研伴"场景的外部 MCP 生态：
- literature_search: 检索文献（参数 topic/limit/recent_years）
- summarize_paper: 摘要压缩（参数 title/abstract/max_words）

用于验证 MCP↔AIP 网关：不改一行 MCP 代码，把工具翻译成 ACPs Partner。
"""

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("yanban-research-tools")


@mcp.tool()
def literature_search(topic: str, limit: int = 10, recent_years: int = 5) -> str:
    """根据主题检索学术文献，返回结构化文献清单（题名/作者/年份/来源/DOI）。"""
    results = [
        f"{i+1}. [{2026 - (i % recent_years)}] 《{topic}研究进展（{i+1}）：方法与实验》 "
        f"作者{i+1} 等 · 计算机学报 · DOI:10.3724/SP.J.1000.{2000+i}"
        for i in range(min(limit, 10))
    ]
    return f"主题「{topic}」检索结果（{len(results)} 篇，近{recent_years}年）：\n" + "\n".join(results)


@mcp.tool()
def summarize_paper(title: str, abstract: str, max_words: int = 100) -> str:
    """将论文摘要压缩为指定字数内的要点摘要。"""
    words = abstract[: max_words * 2]
    return f"《{title}》要点摘要（≤{max_words}词）：{words[:max_words]}…（共{len(abstract)}字压缩）"


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--http":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8022
        mcp.run(transport="streamable-http", host="127.0.0.1", port=port)
    else:
        mcp.run()
