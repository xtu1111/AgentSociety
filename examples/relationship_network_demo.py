import json
import networkx as nx
from bokeh.models import Plot, Range1d, Circle, MultiLine, HoverTool, ColumnDataSource
from bokeh.plotting import from_networkx, output_file, save
from bokeh.io import curdoc
import random

# === 读取 JSON ===
with open("/home/txdazure/githubs/agentsociety_data/agents.json", "r", encoding="utf-8") as f:
    agents = json.load(f)

G = nx.Graph()

# 添加节点
for agent in agents:
    G.add_node(agent["id"],
               name=agent.get("name", ""),
               occupation=agent.get("occupation", ""),
               gender=agent.get("gender", ""),
               attitude=random.choice(["positive", "neutral", "negative"]))  # 先随机态度占位

# 添加边
for agent in agents:
    if "connections" in agent:
        for conn in agent["connections"]:
            source = conn.get("source")
            target = conn.get("target")
            strength = conn.get("strength", 0.5)
            if source and target:
                G.add_edge(source, target, weight=strength, kind=conn.get("kind", ""))

# === 使用 spring_layout，根据权重排布 ===
pos = nx.spring_layout(G, weight='weight', k=1.5, iterations=100, seed=42)

# === Bokeh 绘图 ===
plot = Plot(width=900, height=600,
            x_range=Range1d(-2, 2), y_range=Range1d(-2, 2),
            title="Agent Relationship Network")

# 将位置赋给 NetworkX
graph_renderer = from_networkx(G, pos)

# 节点颜色 & 大小
node_colors = []
for node in G.nodes(data=True):
    attitude = node[1].get("attitude")
    if attitude == "positive":
        node_colors.append("green")
    elif attitude == "negative":
        node_colors.append("red")
    else:
        node_colors.append("gray")

graph_renderer.node_renderer.data_source.data["color"] = node_colors
graph_renderer.node_renderer.glyph = Circle(radius=0.1, fill_color="color")

# 边线样式
graph_renderer.edge_renderer.glyph = MultiLine(line_alpha=0.3, line_width=2)

# Hover 提示
hover = HoverTool(tooltips=[("Name", "@name"), ("Occupation", "@occupation"), ("Gender", "@gender")])
plot.add_tools(hover)

plot.renderers.append(graph_renderer)

output_file("relationship_network_from_json.html")
save(plot)
print("✅ 已生成 relationship_network_from_json.html")
