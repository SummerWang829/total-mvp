# 星桐 TOTAL MVP — 给 Claude Code 的项目规则

产品：面向出海品牌的真人访谈研究 SaaS。MVP 只做 **深访 1:1**：项目 → 受访者 → 邀请+同意 → 三角色房间 → 录制 → 转写 → 带原文回链的洞察 → 片段 → 分享。
不做：定量、爬虫、供应链、座谈会、异步活动、计费（原型里有展示，MVP 不碰）。

## 使用者
用户不会写代码。用中文、简短、直接讲结果；不要贴大段代码；做完说“改了什么、怎么验证的、还差什么”。不确定的决定要问，别擅自定。

## 已拍板的决定（不要改）
- 运营主体在中国；星桐是 processor（处理者）；首批市场只开新加坡（SG）；语言 en / zh / ms / ta。
- 客户不能下载原始录像；保留期：**项目结项后 180 天**删除个人数据，洞察结论保留；未结项的项目 730 天兜底删除。
- 观察员隐身、只读：这由服务端令牌决定（app/livekit.py），不能只靠前端隐藏。
- 引文必须逐字出现在转写里（app/evidence.py）；时间戳取自转写，不取自客户端。
- 受访者设备检测必须通过（麦克风正常；摄像头可不开）才能继续；主持人开始访谈并在候场室“放行”后受访者才能进房；两条都由服务端把关（app/routers/public.py 的 enter）。受访者点“离开”后需要重新放行（2026-10-10 拍板）。
- 访谈房间默认白色浅色，右上角可切换深绿，选择只存在本浏览器；两种配色都要过全部界面检查（2026-10-10 拍板）。
- AI 生成洞察时只写证据的句子编号，引文由程序填入该句完整原文（2026-10-10 拍板，app/providers.py）；人工手写洞察仍可摘录片段。
- AI 主持人（docs/AI主持人_工程与训练任务说明_v1.md）：场次分“人工主持 / AI 主持”（Interview.moderation）。只有 AI 主持的场次，同意书开头披露 AI 并多一个必勾项；用云端模型时必须在 .env 填 MODERATOR_VENDOR，同意书写明数据发给谁，没填就不能建 AI 场次。不自动上线：通过测试只是候选版，由用户确认（2026-10-12）。
- `app/moderator/` 和 `eval/` 的新代码严格遵守用户的《代码质量宪章》：函数 ≤150 行、类 ≤500 行；不许裸 except / except-pass，异常要用 logging 记录（带 traceback，但不记受访者原话）；常量和文案放 constants.py；全部类型注解和 docstring（写明 Raises）；状态用 Enum + 统一的 can_transition 校验；外部模型调用要超时 + 指数退避重试，并通过接口注入以便 mock。tests/test_moderator_setup.py 里有自动检查（2026-10-12）。
- 评估遇到模型服务拒绝（401/402/403/404：密钥失效、余额不足……）必须立刻停下并说明原因，不生成报告；任何报告里出现大量兜底句、`did not end` 或重复提问几千次，先怀疑服务挂了，不要当结果（2026-10-12：DeepSeek 欠费，白跑 4 小时）。启动长评估前先看余额。
- 品牌：星 = 橙色，桐 = 白（深绿底）/ 深绿 #0B382C（白底）；用户侧深绿框架 + 白底 + 橙色强调；不要紫色。

## 进度记录（用户 2026-10-12 要求）
`docs/PROGRESS.md`：每完成一项就更新（日期、做了什么、`scripts/qa.sh` 结果数字、还差什么、风险），**每次向用户汇报前先更新它**。最新的写在最上面。

## 换电脑／离开前必须同步到 GitHub（硬性规则，用户 2026-10-13 要求）
这个项目会在两台电脑（LW、SW）之间交接。GitHub 上只有已推送的内容，没推送就离开，对方拿到的是旧版本。
**不等用户开口，每次自己主动做**（用户 2026-10-13 再次强调）：每到一个稳定停点、每次向用户汇报前、以及用户说要离开／换电脑／交接时，都按顺序做完，才可以说“可以离开”（做到一半的改动提交时在提交说明和 PROGRESS 里标明“进行中”）：
1. 把手头的工作停在稳定的位置（不留跑到一半的改动；有长任务在跑要先告诉用户）。
2. 更新 `docs/PROGRESS.md`，写清楚做到哪、下一步做什么。
3. `git add -A`、`git commit`、`git push`。提交前先看 `git status`，确认没有 `.env`、密钥、数据库、录制文件被带进去（它们应已被忽略）。
4. 运行 `git status` 和 `git log --oneline -3`，把输出原样告诉用户。
5. 只有输出显示“working tree clean，已和 origin/main 同步”，才告诉用户可以离开；否则说明还差什么。
这条规则就是用户对这类推送的长期授权（只推到 origin/main，不推别的仓库、不强推）。
**强制补充（用户 2026-10-14 要求）：每处理完任何一个任务（哪怕只改一个文件、只装了环境、只改了文档），都必须在当次回复结束前更新 GitHub 仓库**（更新 PROGRESS → 提交 → 推送 → 汇报 `git status`）。LW 之后也会继续跟进，所以不存在“小任务可以不推”。没有任何改动时说明“无改动，无需推送”。

## 交付前必须自己测试（硬性规则）
任何改动做完，**先跑 `scripts/qa.sh` 并全绿**，再告诉用户。它依次跑三层：
1. `pytest`（权限、租户隔离、同意、录制上传与合成、转写、证据校验、水印、保留期……）
2. `tests/qa_site.py`：真实浏览器遍历 4 种角色 × 所有页面 × 桌面/手机，检查：控制台错误、横向溢出、死链、字号（≥12px，长文≥14px）、WCAG 对比度、手机点击区 ≥36px、按钮文字不换行、顶栏高度、键盘焦点环、四种语言的受访者页面、减少动效、权限显隐
3. `tests/e2e_room.py`：真实 LiveKit + 3 个真实浏览器走完整条访谈流程（需要 `bin/livekit-server`，`scripts/get_livekit.sh` 会下载）
新增页面/功能时，同步加进 qa_site.py / e2e_room.py 的路由与检查，并加对应 pytest。
改了界面要看截图（qa_shots/）：颜色、字号、对齐、手机排版，肉眼过一遍。
不要为了让检查通过而删检查或放宽阈值；真有理由放宽，先告诉用户。
测试永远不用用户 .env 里的真实密钥和视频服务器（conftest/qa 脚本已强制 mock）。

## 录制的设计（不要改回“只靠服务器录”，除非用户要求）
主持人和受访者的浏览器各自用 MediaRecorder 录自己的摄像头+麦克风，4 秒一片上传（`/recording/{rid}/chunk`，带 part 编号，刷新页面会开新 part）；
停止后后台线程（`app/recording.py` 的 worker）把各 part 拼好、混成一个 MP4。观察员不发布任何东西，所以永远不会被录进去。
转写按说话人音轨分别做，天然知道谁在说话。要换成 LiveKit Egress，只改 recording.py，下游（转写、片段、分享）不用动。

## 安全红线
- 所有查询必须带 org_id（租户隔离）；用 `visible_interview()` 取访谈；对越权返回 404。
- 不在日志、审计、接口里泄露邀请令牌、分享令牌、录制存储路径。
- 受访者页面不显示内部信息（访谈标题、其他受访者、观察员名单）。
- 不提交 .env、密钥、数据库、录制文件。

## 常用命令
- 一条命令跑起来（视频服务器+网站+演示数据）：`scripts/dev.sh` → http://localhost:8000
- 环境检查：`python -m app.doctor`
- 全面检查：`scripts/qa.sh`
- 只跑逻辑测试：`pytest -q`
- 保留期清理：`python -m app.retention`
## 代码地图
app/livekit.py 角色→权限/清场 · app/recording.py 录制合成 · app/pipeline.py 录像→转写 · app/media.py 片段水印 · app/evidence.py 引文校验 · app/retention.py 清理 · app/providers.py ASR/LLM 接口 · app/consent.py 同意文本 ·
app/moderator/ AI 主持人（providers 模型接口、compliance 能否启用、constants 常量与枚举、knowledge 提纲、pacing 时间预算、fsm 阶段状态机、memory 上下文、engine 每轮流水线、guardrails 红线 H1–H12、detectors 停止/危机/问是否 AI 的规则识别、operators 追问算子、templates 固定话术（开场披露、危机热线 995/1767、兜底句）、model_calls 结构化输出、prompts/v00N.json 版本化提示词）· eval/guides 示例提纲（提纲里准备的问题本身也必须过红线，引擎启动时检查）· eval/ 评估：`python -m eval.runner`（--brain mock --participant rule 离线跑通；真评估用 .env 里的模型；--split holdout 只验收，不许据此改规则）；scenarios/generate.py 生成版本化场景集 s001（300 个，20% 保留集）；checkers 规则检查；judges 评分模型（未用 30 段人工分校准前不作依据）；报告在 eval_reports/· eval/ 评估 · eval_reports/ 评估报告 ·
app/ai_room.py + app/routers/ai_room.py AI 主持接入直播间（文字聊天：启动/接管/交还/悄悄话/告警，结束后聊天变成转写，AI 的话标“AI 主持人”；引擎在内存里，服务重启后自动交给真人并红色告警）· app/routers/ 接口 · app/static/ 前端（index 研究员端：左侧深绿导航 + 受访者招募/场次与邀请/直播间/洞察与片段；join 受访者流程：邀请→设备检测→同意→候场室；room 房间：研究员深色三栏、受访者明亮单栏；share 分享、logo.js 凤凰动效）
界面和受访者流程以 docs/星桐TOTAL一体化平台v3_设计规范与受访者端交互说明.md 为准（颜色令牌、字号、组件、受访者 9 步旅程）。
