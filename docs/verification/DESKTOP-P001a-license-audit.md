# P001a 桌面分发依赖许可审计

作者：李亦凡；任务 [#479](https://github.com/LUNARKN1GHT/CampusFlow/issues/479)；父任务 [#467](https://github.com/LUNARKN1GHT/CampusFlow/issues/467)。日期：2026-10-21。

范围：为 Electron 桌面发行包审计后端运行时依赖、前端运行时依赖和随包二进制组件的许可证、分发义务与处理方式。本文是工程合规判断，**不是法律意见**；对外发布前应由项目负责人复核，必要时咨询法律顾问。

## 结论摘要

1. **P001a 审计时，当前依赖集不能直接作为桌面包分发。** `pymupdf==1.28.2` 采用 AGPL-3.0 或 Artifex 商业许可双轨；把 PyMuPDF/MuPDF 与桌面应用一起分发会触发 AGPL 的源码提供义务，除非购买商业许可。项目采用 Apache-2.0，不能在不改变整体分发许可的情况下继续捆绑 PyMuPDF。
2. **推荐替换，P001b 已按推荐落地。** #480 用 `pypdfium2`（BSD-3-Clause／Apache-2.0，内含 PDFium，BSD-3-Clause）加 `Pillow`（MIT-CMU）替换 PyMuPDF；当前分支的 `backend/pyproject.toml` 与 `uv.lock` 不再包含 PyMuPDF，渲染逻辑保持一个函数。替换成本低，且不引入强 copyleft。
3. **替换后没有强 copyleft 阻塞，但不是“零义务”。** `psycopg`／`psycopg-binary` 为 LGPL-3.0-only，需要随包附许可证、提供对应源码获取方式并保持可重新链接；`psycopg-binary` 还捆绑 libpq、OpenSSL、Kerberos、OpenLDAP 等本地库，wheel 只带了 LGPL 文本，打包时必须补齐这些 notice。
4. **Electron／Chromium／Node／PostgreSQL 17／pgvector 都是宽松许可，但版本尚未固定。** Electron、Node、PostgreSQL、pgvector 的精确版本与对应 notice 文件仍需在 P002a／P003a 固定后复核；在版本固定前不能把这几项写成“已验证合规”。
5. **Redis 当前不是桌面包的一部分。** 现有 RQ Worker 依赖 Redis 服务；若最终把 Redis 一并打包，必须单独复核（7.4 起为 RSALv2／SSPLv1，8.x 另含 AGPLv3 选项），本文不把它当作已审计组件。

## 审计方法与证据标记

版本以 `backend/uv.lock` 和 `uv export --frozen --no-dev` 的运行时闭包为准，并与 `uv sync --locked` 后实际安装的 wheel 元数据核对；前端以 `frontend/package-lock.json` 为准。许可证结论不凭印象填写，逐项标注来源：

- **[M]**：已安装 wheel 的 `METADATA`，用 `importlib.metadata.metadata(name)` 读取（`License-Expression`、`License`、`Classifier`）。环境：`backend/.venv`，Python 3.12.13，2026-10-21。
- **[L]**：wheel 内随附的许可证文件（`*.dist-info/licenses/...`、`*.dist-info/LICENSE` 等），逐文件核对。
- **[N]**：npm registry 元数据或 `frontend/package-lock.json` 中的 `license` 字段；Vue 的版权行取自对应 npm tarball 的 `LICENSE`。
- **[U]**：组件未在本地安装时，引用上游官方许可证页面或随二进制分发的许可证文件。
- **[P]**：`uv export --frozen --no-dev --no-hashes --format requirements-txt` 的包清单与版本。

复现命令：

```text
backend$ uv sync --locked
backend$ uv export --frozen --no-dev --no-hashes --format requirements-txt
backend$ uv run python -c "import importlib.metadata as md; print(md.metadata('pypdfium2').get('License'))"
frontend$ npm view electron version license
frontend$ python3 -c "import json; d=json.load(open('package-lock.json')); ..."
```

## 表 1：直接运行时依赖（`backend/pyproject.toml`）

| 组件 | 版本 | 许可证（依据） | 分发义务 | 处理 |
| --- | --- | --- | --- | --- |
| alembic | 1.20.0 | MIT [M][L] | 保留版权与许可声明 | 随包；纳入 notices |
| beautifulsoup4 | 4.15.0 | MIT [M][L] | 保留版权与许可声明 | 随包；纳入 notices |
| cryptography | 46.0.7 | Apache-2.0 OR BSD-3-Clause [M][L] | 附许可证副本；保留 NOTICE（wheel 内无 NOTICE 文件）；修改需说明 | 随包；BSD/Apache 文本随 wheel |
| fastapi | 0.141.1 | MIT [M][L] | 保留版权与许可声明 | 随包；纳入 notices |
| pgvector（Python 客户端） | 0.4.2 | MIT [M][L] | 保留版权与许可声明 | 随包；注意与 pgvector C 扩展许可证不同（见下） |
| pillow | 13.0.0 | MIT-CMU，另含编解码器第三方文本 [M][L] | 保留 Pillow LICENSE 全份 | 随包；P001b 新增 |
| psycopg[binary] | 3.3.6（psycopg-binary 3.3.6） | LGPL-3.0-only [M][L] | 附 LGPL 文本；提供对应源码获取方式；允许替换/重新链接；声明修改 | 可随包；需完成 LGPL 清单（见“发布门槛”） |
| pydantic-settings | 2.15.0 | MIT [M][L] | 保留版权与许可声明 | 随包；纳入 notices |
| pypdf | 6.19.0 | BSD-3-Clause [M][L] | 保留版权、条件与免责声明；不得用作者名背书 | 随包；纳入 notices |
| pypdfium2 | 5.14.0 | BSD-3-Clause／Apache-2.0 [M][L] | 保留 wheel 内 LICENSES 与 PDFium 构建依赖文本 | 随包；P001b 新增 |
| python-docx | 1.2.0 | MIT [M][L] | 保留版权与许可声明 | 随包；纳入 notices |
| python-multipart | 0.0.32 | Apache-2.0 [M][L] | 附许可证副本；保留 NOTICE（如有）；修改需说明 | 随包；纳入 notices |
| rq | 2.12.0 | BSD-2-Clause [M][L] | 保留版权、条件与免责声明 | 随包；纳入 notices |
| sqlalchemy | 2.1.0 | MIT [M][L] | 保留版权与许可声明 | 随包；纳入 notices |
| uvicorn | 0.53.0 | BSD-3-Clause [M][L] | 保留版权、条件与免责声明 | 随包；纳入 notices |

## 表 2：传递运行时依赖（`uv export --no-dev` 闭包）

这些包由直接依赖引入，桌面包同样会携带，不能只审计第一层。

| 组件 | 版本 | 许可证（依据） | 分发义务 | 处理 |
| --- | --- | --- | --- | --- |
| annotated-doc | 0.0.5 | MIT [M][L] | 保留声明 | 随包；纳入 notices |
| annotated-types | 0.8.0 | MIT [M][L] | 保留声明 | 随包；纳入 notices |
| anyio | 4.15.1 | MIT [M][L] | 保留声明 | 随包；纳入 notices |
| cffi | 2.1.1 | MIT-0 [M][L] | MIT-0 不要求保留署名，仍建议保留 | 随包；纳入 notices |
| click | 8.5.0 | BSD-3-Clause [M][L] | 保留声明 | 随包；纳入 notices |
| croniter | 6.2.4 | MIT [M][L] | 保留声明 | 随包；纳入 notices |
| h11 | 0.16.0 | MIT [M][L] | 保留声明 | 随包；纳入 notices |
| idna | 3.20 | BSD-3-Clause [M][L] | 保留声明 | 随包；纳入 notices |
| lxml | 6.1.3 | BSD-3-Clause [M][L] | 保留声明；wheel 另含 libxml2/libxslt 许可证 | 随包；见原生组件 |
| mako | 1.4.3 | MIT [M][L] | 保留声明 | 随包；纳入 notices |
| markupsafe | 3.0.3 | BSD-3-Clause [M][L] | 保留声明 | 随包；纳入 notices |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 [M][L] | 保留 BSD 声明；wheel 内多个组件许可证需一并保留 | 随包；见原生组件 |
| pycparser | 3.11 | BSD-3-Clause [M][L] | 保留声明 | 随包；纳入 notices |
| pydantic | 2.13.5 | MIT [M][L] | 保留声明 | 随包；纳入 notices |
| pydantic-core | 2.46.5 | MIT [M][L] | 保留声明 | 随包；纳入 notices |
| python-dateutil | 2.9.0.post0 | BSD-3-Clause（另有 Apache-2.0 双许可）[M][L] | 保留 BSD 声明 | 随包；纳入 notices |
| python-dotenv | 1.2.3 | BSD-3-Clause [M][L] | 保留声明 | 随包；纳入 notices |
| redis（Python 客户端） | 8.1.0 | MIT [M][L] | 保留声明 | 随包；服务端另见风险项 |
| six | 1.17.0 | MIT [M][L] | 保留声明 | 随包；纳入 notices |
| soupsieve | 3.0.3 | MIT [M][L] | 保留声明 | 随包；纳入 notices |
| starlette | 1.7.0 | BSD-3-Clause [M][L] | 保留声明 | 随包；纳入 notices |
| typing-extensions | 4.16.0 | PSF-2.0 [M][L] | 保留 PSF 许可与版权声明 | 随包；纳入 notices |
| typing-inspection | 0.4.4 | MIT [M][L] | 保留声明 | 随包；纳入 notices |
| tzdata | 2026.4 | **待复核**（仅 Windows 条件依赖；当前 macOS 环境未安装）[P] | 固定 Windows 构建后从 wheel 元数据确认 | Windows 包处理前必须复核 |

## 表 3：随 Python wheel 分发的原生组件

| 组件 | 版本 | 许可证（依据） | 分发义务 | 处理 |
| --- | --- | --- | --- | --- |
| CPython | 3.12.13 | PSF-2.0，另含 OpenSSL 等第三方 [M][U] | 附 PSF 许可证和 CPython 的第三方许可证；当前 uv 管理的发行目录没有顶层 LICENSE 文件 | 打包脚本从 CPython/python-build-standalone 发布物复制完整许可证 |
| OpenSSL（cryptography 内置） | 3.5.6 | Apache-2.0 [L] | 附 Apache-2.0 文本 | wheel 的 `LICENSE.APACHE` 已包含；随包复制 |
| libxml2 / libxslt（lxml 内置） | 2.14.6 / 1.1.43 | MIT [L] | 保留版权与许可声明 | wheel 的 `LICENSES.txt` 已包含；随包复制 |
| NumPy 原生依赖 | 2.5.4 平台构建 | BSD-3-Clause 等 [L] | 保留 wheel 内各组件许可证 | macOS 使用系统 Accelerate，不再分发；Linux/Windows wheel 可能内置 OpenBLAS，须按平台复核 |
| psycopg-binary 内置库 | wheel 3.3.6 | libpq：PostgreSQL License；OpenSSL 3.5.8：Apache-2.0；Kerberos：MIT；OpenLDAP：OpenLDAP Public License；libcom_err：MIT [L][U] | 附各库许可证与版权声明；LGPL 部分提供源码 | **wheel 只带 LGPL 文本，未带上述库的 notice；打包时必须补齐** |
| PyMuPDF / MuPDF | 1.28.2 | AGPL-3.0 或 Artifex 商业 [M][L] | AGPL 源码提供义务 | **已由 #480 移除；不得进入发行包** |
| pypdfium2 / PDFium | 5.14.0 / 156.0.8076.0 | pypdfium2：BSD-3-Clause／Apache-2.0；PDFium：BSD-3-Clause；构建依赖另含 ICU、FreeType、libjpeg-turbo、libpng、zlib 等 [L] | 保留 wheel 内完整 `licenses/LICENSES` 与 `BUILD_LICENSES` | 当前分支已纳入；随包复制整份许可证目录 |
| Pillow | 13.0.0 | MIT-CMU，另含 AOM、libjpeg-turbo、zlib 等第三方 [M][L] | 保留 Pillow LICENSE（其中已汇总第三方文本） | 当前分支已纳入；随包复制 |

## 表 4：桌面外壳、内置服务与前端运行时

| 组件 | 版本 | 许可证（依据） | 分发义务 | 处理 |
| --- | --- | --- | --- | --- |
| Electron | **待固定**（审计时 npm 最新 44.9.3） | MIT [N] | 附 MIT 许可证与版权行 | P002a 固定版本后复制该版本 `LICENSE` |
| Chromium | 随 Electron 固定，**待固定** | BSD-3-Clause 及大量第三方许可证 [U] | 原样附带 Electron 发行包内的 `LICENSES.chromium.html` 及相关 notice | 固定 Electron 版本后作为打包产物校验项 |
| Node.js（Electron 内置） | 随 Electron 固定，**待固定** | MIT，另含 OpenSSL、V8、ICU 等第三方 [U] | 附 Node `LICENSE` 与第三方 notice | 固定 Electron 版本后从对应 Node 源码/发行物复制 |
| PostgreSQL | 17.x（compose 仅固定 `pg17`，补丁版本**待固定**） | PostgreSQL License [U] | 保留版权、许可与免责声明；附带服务端二进制依赖（OpenSSL、ICU、libxml2、zlib 等）的 notice；若附带 `psql` 且链接 readline，另需处理 GPL-3.0 | P003a 固定构建后复制 PostgreSQL `COPYRIGHT` 与依赖许可证 |
| pgvector（C 扩展） | **待固定**（随 `pgvector/pgvector:pg17` 镜像） | PostgreSQL License [U] | 保留版权、许可与免责声明 | P003a 固定版本后纳入 notices |
| Redis（服务端） | **未打包**；当前开发环境为 `redis:7` | 取决于实际版本：7.2 及更早 BSD-3-Clause；7.4+ RSALv2／SSPLv1；8.x 另含 AGPLv3 [U] | 若打包，按实际版本履行对应义务；RSALv2／SSPLv1 不是 OSI 许可 | 推荐桌面模式不打包 Redis（P005 使用数据库轮询）；如必须打包，单独审计并固定版本 |
| Vue | 3.5.43 | MIT [N][L] | 保留版权与许可声明 | 构建产物随包；纳入 notices |
| vue-router | 4.6.4 | MIT [N][L] | 保留版权与许可声明 | 构建产物随包；纳入 notices |
| tzdata（Windows） | 2026.4 | **待复核** | Windows 包固定后确认 | 仅 Windows 条件依赖 |

## 表 5：不随发行包分发的组件

| 组件 | 用途 | 处理 |
| --- | --- | --- |
| pytest、ruff、fakeredis、httpx | 开发/测试 | 不进入 `uv sync --no-dev` 运行时闭包；不随包 |
| Vite、TypeScript、vue-tsc、Playwright、esbuild、Rollup | 前端构建/测试 | 只在构建机使用；不随包；其许可证无需进入产品 notice（构建产物中的第三方代码另行处理） |
| Docker、uv、RQ Worker 基础镜像 | 构建/开发工具 | 不随桌面包 |
| 远程模型服务（智谱、DeepSeek 等） | API 调用 | 不随包；服务条款另行评估，不属于本审计 |

## PyMuPDF 三选一

| 方案 | 许可结果 | 成本/工作量 | 风险 | 结论 |
| --- | --- | --- | --- | --- |
| 替换为 pypdfium2 + Pillow | pypdfium2 BSD-3/Apache-2.0，PDFium BSD-3，Pillow MIT-CMU；无强 copyleft | 一个渲染函数 + 测试 + 锁文件，约半天 | PDFium 渲染结果与 MuPDF 存在像素差异；需按 150 dpi 验收 | **推荐** |
| 购买 Artifex 商业许可 | 可继续使用 PyMuPDF，免除 AGPL 源码义务 | 需询价、采购、合同与续费管理 | 成本与供应商依赖；仅为一个页面渲染功能不划算 | 不推荐 |
| 接受 AGPL-3.0 分发 | 无需购买，但整个桌面组合作品须按 AGPL 提供源码，并满足网络交互用户获取源码等义务 | 需调整项目整体许可证、源码发布与合规流程 | 与当前 Apache-2.0 分发策略冲突；后续无法闭源分发 | 不可接受 |

补充：PyMuPDF 的 AGPL 义务不因“只在本地渲染、不联网”而消失；把库随安装包分发本身就是分发行为。商业许可的具体条款与价格未在本文核实，需向 Artifex 获取报价。

## 能否随当前依赖集发布

**P001a 审计时（依赖集仍含 PyMuPDF）：不可以（NO-GO）。** 唯一阻断项是 `pymupdf==1.28.2` 的 AGPL-3.0。其余组件为 MIT、BSD、Apache-2.0、PSF、PostgreSQL License 或 LGPL；LGPL 需要履行通知与源码义务，但不阻止分发。

**P001b 完成后（当前分支）：有条件可以（conditional GO）。**

已完成：

- 移除 `pymupdf`，加入 `pypdfium2==5.14.0` 与 `Pillow==13.0.0`，更新 `uv.lock`；`backend/src`、`backend/tests` 与 `scripts` 不再导入 `pymupdf`／`fitz`。
- 渲染行为由 `docs/verification/DESKTOP-P001b-pdf-render.md` 的测试覆盖：150 dpi 像素尺寸、扫描页、页码越界、损坏 PDF。

发布前仍需完成：

- 随包附带 `THIRD-PARTY-NOTICES.md`、仓库根 `LICENSE`、各 wheel 的许可证文件，以及 Electron 的 `LICENSES.chromium.html`。
- 为 psycopg 的 LGPL 部分提供源码获取方式（PyPI sdist 链接或书面要约），并保持动态链接、可替换。
- 固定 Electron、Node、PostgreSQL、pgvector 的精确版本，逐项补齐 notice；这几项在固定前保持“待复核”，不能写成“已合规”。
- 若最终打包 Redis，单独完成其许可证审计。

## 参考来源

- PyMuPDF 双许可说明：`pymupdf==1.28.2` 的 wheel `METADATA` 与 `COPYING`；<https://pymupdf.readthedocs.io/en/latest/about.html#license>
- Electron 许可证元数据：`npm view electron version license`（2026-10-21 返回 `44.9.3` / `MIT`）；<https://github.com/electron/electron/blob/main/LICENSE>
- Chromium 与 Node 第三方许可证：以 Electron 发行包内的 `LICENSES.chromium.html` 和对应 Node.js 发行物的 `LICENSE` 为准；<https://github.com/nodejs/node/blob/main/LICENSE>
- PostgreSQL License：<https://www.postgresql.org/about/licence/>
- pgvector License：<https://github.com/pgvector/pgvector/blob/master/LICENSE>（GitHub API 返回 PostgreSQL License 文本，版权行为 1996-2026 PostgreSQL GDG 与 1994 Regents of the University of California）
- psycopg LGPL：`psycopg==3.3.6`／`psycopg-binary==3.3.6` wheel 内 `licenses/LICENSE.txt`；<https://www.psycopg.org/>
- pypdfium2 与 PDFium：`pypdfium2==5.14.0` wheel 内 `licenses/LICENSES` 与 `licenses/data/<平台>/BUILD_LICENSES`；<https://github.com/pypdfium2-team/pypdfium2>
- Pillow：`pillow==13.0.0` wheel 内 `licenses/LICENSE`；<https://python-pillow.org/>
- Vue / vue-router：npm tarball 的 `LICENSE` 与 `frontend/package-lock.json` 的 `license` 字段；<https://github.com/vuejs/core>、<https://github.com/vuejs/router>

## 未覆盖与限制

- 本文不是法律意见，也不构成对任何第三方许可证的官方解释。
- Electron、Chromium、Node、PostgreSQL、pgvector 的精确版本和随包 notice 尚未固定；本文只确认了当前可获得的许可证类型，未逐行审阅 Chromium/Node 的完整第三方清单。
- `psycopg-binary` wheel 没有随附其内置 libpq、OpenSSL、Kerberos、OpenLDAP 等库的许可证文本；这是打包阶段必须补齐的义务缺口，不是“已合规”。
- macOS 审计结果不能直接外推到 Windows/Linux：NumPy 的 BLAS、tzdata、PostgreSQL 构建和 Python 运行时的随包组件可能不同。
- 未审计 Redis 服务端、Docker 基础镜像、模型供应商服务条款和未来新增的前端依赖。
- PyMuPDF 的 AGPL 全文未随 wheel 提供（wheel 只有双许可说明），这不影响“移除”结论；若改走 AGPL 路线，需另行取得完整许可证文本并履行源码义务。
