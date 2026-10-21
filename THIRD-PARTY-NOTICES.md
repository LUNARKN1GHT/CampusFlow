# CampusFlow 第三方软件声明（草案）

本文件列出 CampusFlow 桌面发行包中随附的第三方软件、版权声明和许可证文本。分发时必须把本文件与仓库根目录的 `LICENSE`（Apache-2.0）一起放入安装包，并按“随包必须复制的许可证文件”一节附带各组件原始许可证文件。

**状态：草案。** 标记 **[待固定]** 的组件尚未锁定精确版本，其版权行和 notice 必须在版本固定后从对应发行物复制；在固定前不能把相关条目视为已完成合规复核。本文是工程整理，不是法律意见。

最近更新：2026-10-21（P001a #479）。P001b #480 替换 PDF 渲染依赖后，删除了 PyMuPDF，新增 pypdfium2 与 Pillow。

## 1. 分发范围

| 类别 | 组件 | 版本状态 |
| --- | --- | --- |
| 后端 Python 运行时 | `uv.lock` 中 `uv export --no-dev` 的完整闭包 | 已固定（2026-10-21 锁文件） |
| 前端运行时 | Vue、vue-router 及其运行时依赖 | 已固定（`frontend/package-lock.json`） |
| 桌面外壳 | Electron、Chromium、Node.js | **[待固定]**（P002a） |
| 内置数据库 | PostgreSQL 17、pgvector | **[待固定]**（P003a；当前 compose 仅固定 `pg17`） |
| 文档渲染 | pypdfium2、PDFium、Pillow | pypdfium2 5.14.0 / PDFium 156.0.8076.0 / Pillow 13.0.0 |
| 不随包 | pytest、ruff、Vite、TypeScript、Playwright、Redis 服务端、Docker | 不进入发行包；Redis 若改为随包需单独审计 |

## 2. MIT License

以下组件采用 MIT 许可证。版权行来自各包 wheel 或许可证文件；Electron 的版权行在版本固定后从对应 npm 包 `LICENSE` 复制。

- Alembic — Copyright 2009-2026 Michael Bayer.
- annotated-doc — Copyright (c) 2025 Sebastián Ramírez
- annotated-types — Copyright (c) 2022 the contributors
- anyio — Copyright (c) 2018 Alex Grönholm
- Beautiful Soup — Copyright (c) Leonard Richardson
- croniter — Copyright (C) 2010-2012 Matsumoto Taichi
- FastAPI — Copyright (c) 2018 Sebastián Ramírez
- h11 — Copyright (c) 2016 Nathaniel J. Smith <njs@pobox.com> and other contributors
- Mako — Copyright 2006-2026 the Mako authors and contributors
- pgvector（Python 客户端） — Copyright (c) 2021-2025 Andrew Kane
- Pydantic — Copyright (c) 2017 to present Pydantic Services Inc. and individual contributors.
- pydantic-core — Copyright (c) 2022 Samuel Colvin
- pydantic-settings — Copyright (c) 2022 Samuel Colvin and other contributors
- python-docx — Copyright (c) 2013 Steve Canny
- redis（Python 客户端） — Copyright (c) 2022-2023, Redis, inc.
- six — Copyright (c) 2010-2024 Benjamin Peterson
- soupsieve — Copyright (c) 2018 - 2026 Isaac Muse
- SQLAlchemy — Copyright 2005-2026 SQLAlchemy authors and contributors
- typing-inspection — Copyright (c) Pydantic Services Inc. 2025 to present
- Vue 及 `@vue/*` 运行时包 — Copyright (c) 2018-present, Yuxi (Evan) You
- vue-router — Copyright (c) 2019-present Eduardo San Martin Morote
- Electron — **[待固定]**，MIT；版权行从锁定版本的 `LICENSE` 复制

```text
MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 3. MIT No Attribution（MIT-0）

cffi 2.1.1 采用 MIT-0；版权行以 wheel 内 `LICENSE` 为准。MIT-0 不要求保留署名，本文件仍主动列出。

```text
MIT No Attribution

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 4. BSD 三条款许可证（BSD-3-Clause）

以下组件采用 BSD-3-Clause：

- click — Copyright 2014 Pallets
- idna — Copyright (c) 2013-2026, Kim Davies and contributors.
- lxml — Copyright (c) 2004 Infrae. All rights reserved.
- MarkupSafe — Copyright 2010 Pallets
- NumPy — Copyright (c) 2005-2025, NumPy Developers.
- pycparser — Copyright (c) 2008-2027, Eli Bendersky
- pypdf — Copyright (c) 2006-2008, Mathieu Fenniak
- python-dateutil — Copyright 2017- Paul Ganssle <paul@ganssle.io>；Copyright 2017- dateutil contributors。该项目为 BSD-3-Clause 与 Apache-2.0 双许可，本文件按 BSD-3-Clause 履行。
- Starlette — Copyright © 2018, Encode OSS Ltd.
- Uvicorn — Copyright © 2017-present, Encode OSS Ltd.
- pypdfium2 5.14.0 — BSD-3-Clause（另可选 Apache-2.0）；版权行见 wheel 内 `licenses/LICENSES/BSD-3-Clause.txt`。
- PDFium 156.0.8076.0 — BSD-3-Clause；版权行见 wheel 内 `licenses/data/<平台>/BUILD_LICENSES/pdfium.txt`。
- Chromium — **[待固定]**；BSD-3-Clause 及大量第三方许可证，见 Electron 发行包内的 `LICENSES.chromium.html`。

```text
BSD 3-Clause License

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## 5. BSD 二条款许可证（BSD-2-Clause）

RQ 2.12.0 — Copyright 2012 Vincent Driessen. All rights reserved.

```text
BSD 2-Clause License

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## 6. Apache License 2.0

以下组件采用 Apache-2.0：

- cryptography 46.0.7（Apache-2.0 OR BSD-3-Clause；本文件同时保留 BSD 文本）
- python-multipart 0.0.32
- OpenSSL 3.5.6（cryptography 内置）与 OpenSSL 3.5.8（psycopg-binary 内置）
- pypdfium2 的 Apache-2.0 可选许可（本文件按 BSD-3-Clause 履行）

Apache-2.0 完整文本见仓库根目录 `LICENSE`；分发时该文件必须随包。若组件 wheel 内带有 `NOTICE` 文件，必须原样保留；当前审计的上述 Python wheel 未发现 `NOTICE` 文件。

## 7. Python Software Foundation License 2.0（PSF-2.0）

适用组件：typing-extensions 4.16.0、CPython 3.12.13（PyInstaller sidecar 携带）。

CPython 的完整 `LICENSE` 还包含 BeOpen、CNRI、CWI 等历史许可证和第三方组件声明；打包时必须复制 CPython 发行物中的完整许可证文件。当前 uv 管理的 `cpython-3.12.13-macos-aarch64-none` 安装目录没有顶层 `LICENSE`，需从 python-build-standalone 对应发布物或 CPython 源码补齐。

```text
PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2

1. This LICENSE AGREEMENT is between the Python Software Foundation
("PSF"), and the Individual or Organization ("Licensee") accessing and
otherwise using this software ("Python") in source or binary form and
its associated documentation.

2. Subject to the terms and conditions of this License Agreement, PSF hereby
grants Licensee a nonexclusive, royalty-free, world-wide license to reproduce,
analyze, test, perform and/or display publicly, prepare derivative works,
distribute, and otherwise use Python alone or in any derivative version,
provided, however, that PSF's License Agreement and PSF's notice of copyright,
i.e., "Copyright (c) 2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009, 2010,
2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023 Python Software Foundation;
All Rights Reserved" are retained in Python alone or in any derivative version
prepared by Licensee.

3. In the event Licensee prepares a derivative work that is based on
or incorporates Python or any part thereof, and wants to make
the derivative work available to others as provided herein, then
Licensee hereby agrees to include in any such work a brief summary of
the changes made to Python.

4. PSF is making Python available to Licensee on an "AS IS"
basis.  PSF MAKES NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR
IMPLIED.  BY WAY OF EXAMPLE, BUT NOT LIMITATION, PSF MAKES NO AND
DISCLAIMS ANY REPRESENTATION OR WARRANTY OF MERCHANTABILITY OR FITNESS
FOR ANY PARTICULAR PURPOSE OR THAT THE USE OF PYTHON WILL NOT
INFRINGE ANY THIRD PARTY RIGHTS.

5. PSF SHALL NOT BE LIABLE TO LICENSEE OR ANY OTHER USERS OF PYTHON
FOR ANY INCIDENTAL, SPECIAL, OR CONSEQUENTIAL DAMAGES OR LOSS AS
A RESULT OF MODIFYING, DISTRIBUTING, OR OTHERWISE USING PYTHON,
OR ANY DERIVATIVE THEREOF, EVEN IF ADVISED OF THE POSSIBILITY THEREOF.

6. This License Agreement will automatically terminate upon a material
breach of its terms and conditions.

7. Nothing in this License Agreement shall be deemed to create any
relationship of agency, partnership, or joint venture between PSF and
Licensee.  This License Agreement does not grant permission to use PSF
trademarks or trade name in a trademark sense to endorse or promote
products or services of Licensee, or any third party.

8. By copying, installing or otherwise using Python, Licensee
agrees to be bound by the terms and conditions of this License
Agreement.
```

## 8. GNU Lesser General Public License 3.0（LGPL-3.0-only）

适用组件：psycopg 3.3.6、psycopg-binary 3.3.6。

LGPL-3.0 以 GPL-3.0 为基础并附加例外；分发时还必须附带 GPL-3.0 完整文本。psycopg wheel 只提供 LGPL 文本，GPL-3.0 文本需从 <https://www.gnu.org/licenses/gpl-3.0.txt> 或对应源码包复制。

```text
                   GNU LESSER GENERAL PUBLIC LICENSE
                       Version 3, 29 June 2007

 Copyright (C) 2007 Free Software Foundation, Inc. <https://fsf.org/>
 Everyone is permitted to copy and distribute verbatim copies
 of this license document, but changing it is not allowed.


  This version of the GNU Lesser General Public License incorporates
the terms and conditions of version 3 of the GNU General Public
License, supplemented by the additional permissions listed below.

  0. Additional Definitions.

  As used herein, "this License" refers to version 3 of the GNU Lesser
General Public License, and the "GNU GPL" refers to version 3 of the GNU
General Public License.

  "The Library" refers to a covered work governed by this License,
other than an Application or a Combined Work as defined below.

  An "Application" is any work that makes use of an interface provided
by the Library, but which is not otherwise based on the Library.
Defining a subclass of a class defined by the Library is deemed a mode
of using an interface provided by the Library.

  A "Combined Work" is a work produced by combining or linking an
Application with the Library.  The particular version of the Library
with which the Combined Work was made is also called the "Linked
Version".

  The "Minimal Corresponding Source" for a Combined Work means the
Corresponding Source for the Combined Work, excluding any source code
for portions of the Combined Work that, considered in isolation, are
based on the Application, and not on the Linked Version.

  The "Corresponding Application Code" for a Combined Work means the
object code and/or source code for the Application, including any data
and utility programs needed for reproducing the Combined Work from the
Application, but excluding the System Libraries of the Combined Work.

  1. Exception to Section 3 of the GNU GPL.

  You may convey a covered work under sections 3 and 4 of this License
without being bound by section 3 of the GNU GPL.

  2. Conveying Modified Versions.

  If you modify a copy of the Library, and, in your modifications, a
facility refers to a function or data to be supplied by an Application
that uses the facility (other than as an argument passed when the
facility is invoked), then you may convey a copy of the modified
version:

   a) under this License, provided that you make a good faith effort to
   ensure that, in the event an Application does not supply the
   function or data, the facility still operates, and performs
   whatever part of its purpose remains meaningful, or

   b) under the GNU GPL, with none of the additional permissions of
   this License applicable to that copy.

  3. Object Code Incorporating Material from Library Header Files.

  The object code form of an Application may incorporate material from
a header file that is part of the Library.  You may convey such object
code under terms of your choice, provided that, if the incorporated
material is not limited to numerical parameters, data structure
layouts and accessors, or small macros, inline functions and templates
(ten or fewer lines in length), you do both of the following:

   a) Give prominent notice with each copy of the object code that the
   Library is used in it and that the Library and its use are
   covered by this License.

   b) Accompany the object code with a copy of the GNU GPL and this license
   document.

  4. Combined Works.

  You may convey a Combined Work under terms of your choice that,
taken together, effectively do not restrict modification of the
portions of the Library contained in the Combined Work and reverse
engineering for debugging such modifications, if you also do each of
the following:

   a) Give prominent notice with each copy of the Combined Work that
   the Library is used in it and that the Library and its use are
   covered by this License.

   b) Accompany the Combined Work with a copy of the GNU GPL and this license
   document.

   c) For a Combined Work that displays copyright notices during
   execution, include the copyright notice for the Library among
   these notices, as well as a reference directing the user to the
   copies of the GNU GPL and this license document.

   d) Do one of the following:

       0) Convey the Minimal Corresponding Source under the terms of this
       License, and the Corresponding Application Code in a form
       suitable for, and under terms that permit, the user to
       recombine or relink the Application with a modified version of
       the Linked Version to produce a modified Combined Work, in the
       manner specified by section 6 of the GNU GPL for conveying
       Corresponding Source.

       1) Use a suitable shared library mechanism for linking with the
       Library.  A suitable mechanism is one that (a) uses at run time
       a copy of the Library already present on the user's computer
       system, and (b) will operate properly with a modified version
       of the Library that is interface-compatible with the Linked
       Version.

   e) Provide Installation Information, but only if you would otherwise
   be required to provide such information under section 6 of the
   GNU GPL, and only to the extent that such information is
   necessary to install and execute a modified version of the
   Combined Work produced by recombining or relinking the
   Application with a modified version of the Linked Version. (If
   you use option 4d0, the Installation Information must accompany
   the Minimal Corresponding Source and Corresponding Application
   Code. If you use option 4d1, you must provide the Installation
   Information in the manner specified by section 6 of the GNU GPL
   for conveying Corresponding Source.)

  5. Combined Libraries.

  You may place library facilities that are a work based on the
Library side by side in a single library together with other library
facilities that are not Applications and are not covered by this
License, and convey such a combined library under terms of your
choice, if you do both of the following:

   a) Accompany the combined library with a copy of the same work based
   on the Library, uncombined with any other library facilities,
   conveyed under the terms of this License.

   b) Give prominent notice with the combined library that part of it
   is a work based on the Library, and explaining where to find the
   accompanying uncombined form of the same work.

  6. Revised Versions of the GNU Lesser General Public License.

  The Free Software Foundation may publish revised and/or new versions
of the GNU Lesser General Public License from time to time. Such new
versions will be similar in spirit to the present version, but may
differ in detail to address new problems or concerns.

  Each version is given a distinguishing version number. If the
Library as you received it specifies that a certain numbered version
of the GNU Lesser General Public License "or any later version"
applies to it, you have the option of following the terms and
conditions either of that published version or of any later version
published by the Free Software Foundation. If the Library as you
received it does not specify a version number of the GNU Lesser
General Public License, you may choose any version of the GNU Lesser
General Public License ever published by the Free Software Foundation.

  If the Library as you received it specifies that a proxy can decide
whether future versions of the GNU Lesser General Public License shall
apply, that proxy's public statement of acceptance of any version is
permanent authorization for you to choose that version for the
Library.
```

### 8.1 psycopg 源码获取

- psycopg 3.3.6 源码：<https://pypi.org/project/psycopg/3.3.6/#files>（sdist）
- psycopg-binary 3.3.6 源码：<https://pypi.org/project/psycopg-binary/3.3.6/#files>（sdist）
- psycopg-binary wheel 同时包含 libpq、OpenSSL、Kerberos、OpenLDAP、libcom_err 的本地库。其许可证文本未随 wheel 提供，发行包必须从各上游项目补齐；对应源码获取方式随发行说明一并提供。

## 9. PostgreSQL License

适用组件：PostgreSQL 17（**[待固定]**，P003a）、pgvector C 扩展（**[待固定]**，随 `pgvector/pgvector:pg17` 镜像）。

```text
PostgreSQL Database Management System
(also known as Postgres, formerly as Postgres95)

Portions Copyright (c) 1996-2026, The PostgreSQL Global Development Group

Portions Copyright (c) 1994, The Regents of the University of California

Permission to use, copy, modify, and distribute this software and its
documentation for any purpose, without fee, and without a written agreement
is hereby granted, provided that the above copyright notice and this
paragraph and the following two paragraphs appear in all copies.

IN NO EVENT SHALL THE UNIVERSITY OF CALIFORNIA BE LIABLE TO ANY PARTY FOR
DIRECT, INDIRECT, SPECIAL, INCIDENTAL, OR CONSEQUENTIAL DAMAGES, INCLUDING
LOST PROFITS, ARISING OUT OF THE USE OF THIS SOFTWARE AND ITS
DOCUMENTATION, EVEN IF THE UNIVERSITY OF CALIFORNIA HAS BEEN ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.

THE UNIVERSITY OF CALIFORNIA SPECIFICALLY DISCLAIMS ANY WARRANTIES,
INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS FOR A PARTICULAR PURPOSE.  THE SOFTWARE PROVIDED HEREUNDER IS
ON AN "AS IS" BASIS, AND THE UNIVERSITY OF CALIFORNIA HAS NO OBLIGATIONS TO
PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS, OR MODIFICATIONS.
```

PostgreSQL 发行物还包含 OpenSSL、ICU、libxml2、zlib 等依赖的许可证，统一列在 PostgreSQL 源码的 `COPYRIGHT` 与 `doc/` 目录中；打包时必须复制。若附带 `psql` 且其链接了 readline，readline 的 GPL-3.0 义务需另行处理。

## 10. MIT-CMU（Pillow）

Pillow 13.0.0 采用 MIT-CMU。Pillow 的 `LICENSE` 文件同时汇总了 AOM、libjpeg-turbo、zlib 等随 wheel 分发组件的许可证，必须整份复制。

```text
The Python Imaging Library (PIL) is

    Copyright © 1997-2011 by Secret Labs AB
    Copyright © 1995-2011 by Fredrik Lundh and contributors

Pillow is the friendly PIL fork. It is

    Copyright © 2010 by Jeffrey 'Alex' Clark and contributors

Like PIL, Pillow is licensed under the open source MIT-CMU License:

By obtaining, using, and/or copying this software and/or its associated
documentation, you agree that you have read, understood, and will comply
with the following terms and conditions:

Permission to use, copy, modify and distribute this software and its
documentation for any purpose and without fee is hereby granted,
provided that the above copyright notice appears in all copies, and that
both that copyright notice and this permission notice appear in supporting
documentation, and that the name of Secret Labs AB or the author not be
used in advertising or publicity pertaining to distribution of the software
without specific, written prior permission.

SECRET LABS AB AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS
SOFTWARE, INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS.
IN NO EVENT SHALL SECRET LABS AB OR THE AUTHOR BE LIABLE FOR ANY SPECIAL,
INDIRECT OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE
OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.
```

## 11. Electron、Chromium 与 Node.js

这三个组件的精确版本在本文写作时尚未固定，**不得把本节视为已完成 notice 整理**。

- **Electron [待固定]**：MIT。版本固定后，把该版本 npm 包内的 `LICENSE` 原样复制到发行包。
- **Chromium [待固定]**：BSD-3-Clause 及大量第三方许可证。Electron 发行包内提供 `LICENSES.chromium.html`；打包时必须原样附带，不能只保留 BSD-3 文本。
- **Node.js（Electron 内置）[待固定]**：MIT，另含 OpenSSL、V8、ICU 等第三方组件。版本固定后，从对应 Node.js 发行物复制 `LICENSE` 和第三方 notice。

固定版本时应同时记录 Electron 版本、其对应的 Chromium 版本和 Node.js 版本，并把上述文件加入发行包校验清单。

## 12. 已移除：PyMuPDF / MuPDF

PyMuPDF 1.28.2 采用 AGPL-3.0 或 Artifex 商业许可。P001b #480 已用 pypdfium2 + Pillow 替换 `backend/src/campusflow/infrastructure/parsers/pdf_render.py`，**本发行包不得再包含 PyMuPDF 或 MuPDF**。如果旧构建产物仍包含 `pymupdf`／`fitz`，该产物不具备本文件声明的分发条件。

## 13. 条件组件：Redis 服务端

当前桌面包不包含 Redis 服务端；RQ Worker 使用的 Redis 是开发环境服务。若后续把 Redis 随包分发，必须按实际版本处理：7.2 及更早为 BSD-3-Clause，7.4 起为 RSALv2／SSPLv1，8.x 另提供 AGPLv3 选项。推荐桌面模式使用数据库轮询 Worker（P005），避免引入该依赖。

## 14. 随包必须复制的许可证文件

| 来源 | 目标 | 说明 |
| --- | --- | --- |
| 仓库根 `LICENSE` | 安装包根目录 `LICENSE` | CampusFlow 自身 Apache-2.0 |
| 本文件 | 安装包根目录 `THIRD-PARTY-NOTICES.md` | 第三方汇总 |
| 各 Python wheel 的 `*.dist-info/licenses/`、`*.dist-info/LICENSE` | `licenses/python/<包名>/` | 逐包复制；不要只复制本文件 |
| `cryptography` 的 `LICENSE`、`LICENSE.APACHE`、`LICENSE.BSD` | `licenses/python/cryptography/` | 含 OpenSSL 3.5.6 的 Apache 文本 |
| `lxml` 的 `LICENSE.txt`、`LICENSES.txt` | `licenses/python/lxml/` | 含 libxml2/libxslt |
| `numpy` 的 `licenses/` 整个目录 | `licenses/python/numpy/` | 含 BSD/0BSD/MIT/Zlib/CC0 等 |
| `psycopg`／`psycopg-binary` 的 `LICENSE.txt` | `licenses/python/psycopg/` | LGPL-3.0；另补 GPL-3.0 全文 |
| `pypdfium2` 的 `licenses/` 整个目录 | `licenses/python/pypdfium2/` | 含 PDFium 及构建依赖 |
| `Pillow` 的 `LICENSE` | `licenses/python/pillow/` | 含第三方编解码器文本 |
| Electron 的 `LICENSE`、`LICENSES.chromium.html` | `licenses/electron/` | **[待固定]** 版本后复制 |
| Node.js 的 `LICENSE` 与第三方 notice | `licenses/node/` | **[待固定]** 版本后复制 |
| PostgreSQL 的 `COPYRIGHT` 及依赖许可证 | `licenses/postgresql/` | **[待固定]** 构建后复制 |
| pgvector 的 `LICENSE` | `licenses/pgvector/` | **[待固定]** 版本后复制 |

## 15. 版本固定后的复核清单

- [ ] Electron 版本、Chromium 版本、Node.js 版本已记录，`LICENSE` 与 `LICENSES.chromium.html` 已随包。
- [ ] PostgreSQL 17 补丁版本、pgvector 扩展版本已记录，`COPYRIGHT` 与依赖许可证已随包。
- [ ] `psycopg-binary` 内置 libpq、OpenSSL、Kerberos、OpenLDAP、libcom_err 的许可证文本已补齐。
- [ ] CPython 3.12.13 的完整 `LICENSE` 已从 python-build-standalone/CPython 源码补齐。
- [ ] LGPL/GPL 源码获取方式已写入发行说明。
- [ ] Windows 构建的 `tzdata` 许可证已复核。
- [ ] `rg -n "pymupdf|import fitz" backend/src backend/tests scripts` 无结果。
