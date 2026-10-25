# Companion API（/companion 前缀，leader.py 挂载）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /companion/chat | {user_text, project_id?} → {project_id, state, reply, intent, papers?} |
| POST | /companion/project | 创建项目 → {project_id} |
| GET | /companion/project/{id} | 快照（state/intent/messages/papers） |
| POST | /companion/search | {project_id, limit} → 立即按 intent 检索并入库 |
| POST | /companion/paper/feedback | {project_id, paper, status, reason} → 写 Paper Memory |
| GET | /companion/paper/{project_id}?status= | 论文列表（可按状态过滤） |
| GET | /companion/map/{project_id} | 研究地图 v1 |
| POST | /companion/paper/fetch-pdf | {project_id, paper} → 解析下载 OA PDF |
| GET | /companion/paper/{project_id}/{file} | 流式返回 PDF |
| POST | /companion/paper/{project_id}/{file}/parse | 章节解析预览 |
| POST | /companion/paper/{project_id}/qa | {question, filename} → 论文上下文问答 |
| GET | /companion/memory/{user_id} | 用户画像快照 |

错误语义：404=资源不存在；400=参数非法；检索/下载失败不抛 5xx，返回空结果或明确 404。
