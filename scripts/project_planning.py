"""Validate, render, initially publish, and audit the CampusFlow planning snapshot.

Publishing is resumable through stable body markers. Existing issue content is never
overwritten; after initial publication, GitHub is authoritative for task progress.
Uses the authenticated gh CLI, never reads or prints its credentials.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "docs" / "planning"
PLAN = DIRECTORY / "backlog.json"
MAP = DIRECTORY / "github-map.json"
MARKER = re.compile(r"<!-- campusflow-plan:([A-Z0-9]+) -->")


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def validate(plan):
    issues = {x["key"]: x for x in plan["issues"]}
    assert len(issues) == len(plan["issues"]), "Duplicate planning keys"
    assert len({x["title"] for x in issues.values()}) == len(issues), "Duplicate titles"
    labels = {x["name"] for x in plan["labels"]}
    milestones = {x["key"]: x for x in plan["milestones"]}
    visiting, visited, ordered = set(), set(), []

    def visit(key):
        assert key in issues, f"Unknown dependency {key}"
        assert key not in visiting, f"Dependency cycle at {key}"
        if key in visited:
            return
        visiting.add(key)
        item = issues[key]
        for dep in item["depends_on"]:
            visit(dep)
            upstream = issues[dep]
            if (
                item["milestone"]
                and upstream["milestone"]
                and upstream["state"] != "closed"
            ):
                assert upstream["milestone"] <= item["milestone"], (
                    f"Later milestone: {key} <- {dep}"
                )
        visiting.remove(key)
        visited.add(key)
        ordered.append(item)

    for item in issues.values():
        assert item["milestone"] is None or item["milestone"] in milestones
        assert set(item["labels"]) <= labels, item["key"]
        assert item["scope"] and len(item["acceptance"]) >= 2, item["key"]
        assert item["source"] and len(item["title"]) < 180
        if item["parent"]:
            parent = issues[item["parent"]]
            assert parent["kind"] == "epic" and parent["milestone"] == item["milestone"]
        else:
            assert item["kind"] == "epic"
        if item["state"] == "closed":
            assert item.get("evidence") and "status:backfilled" in item["labels"]
        visit(item["key"])
    for parent in [x for x in issues.values() if x["kind"] == "epic"]:
        children = [x for x in issues.values() if x["parent"] == parent["key"]]
        assert 1 <= len(children) <= 100
    for ms in milestones.values():
        assert len(ms["gates"]) >= 3 and all(d in milestones for d in ms["depends_on"])
    return ordered


def api(endpoint, payload=None, paginate=False):
    args = ["gh", "api", endpoint]
    if paginate:
        args += ["--paginate", "--slurp"]
    if payload is not None:
        args += ["--input", "-"]
    result = subprocess.run(
        args,
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        # Stop on rate limits or ambiguous mutations. Rerun re-reads markers and
        # recovers successful creations rather than repeating a blind POST.
        raise RuntimeError(f"{endpoint}: {result.stderr.strip()} {result.stdout[:400]}")
    value = json.loads(result.stdout) if result.stdout.strip() else None
    if isinstance(value, dict) and value.get("errors"):
        raise RuntimeError(json.dumps(value["errors"], ensure_ascii=False))
    return [item for page in value for item in page] if paginate else value


def mutate(endpoint, payload, method=None):
    # Serial content creation, below GitHub's per-minute secondary limits.
    time.sleep(1.25)
    if method is None:
        return api(endpoint, payload)
    result = subprocess.run(
        ["gh", "api", endpoint, "--method", method, "--input", "-"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"{endpoint}: {result.stderr.strip()} {result.stdout[:400]}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def milestone_body(item):
    return "\n".join(
        [
            f"阶段：{item['phase']}。{item['goal']}",
            "",
            "完成条件：",
            *[f"- {x}" for x in item["gates"]],
            "",
            "前置验收：" + ("、".join(item["depends_on"]) or "无"),
            "版本目标：" + (item["release_tag"] or "基线准备，不创建版本 tag"),
            "不设虚构截止日期；全部子任务验收后由组长关闭。父 Issue 不重复计入工作量。",
            "已合并实现的补录仅表示代码存在，集成复验和阶段验收独立追踪。",
        ]
    )


def reference(key, mapping):
    row = mapping.get("issues", {}).get(key)
    return f"#{row['number']}（{key}）" if row else f"`{key}`"


def issue_body(item, plan, mapping):
    milestones = {x["key"]: x for x in plan["milestones"]}
    stage = (
        milestones[item["milestone"]]["title"]
        if item["milestone"]
        else "后续探索：未承诺开发阶段或日期"
    )
    lines = [
        f"<!-- campusflow-plan:{item['key']} -->",
        "",
        f"规划编号：**{item['key']}** · {stage}",
        "",
        "## 目标与交付物",
        "",
        item["scope"],
        "",
        "## 验收条件",
        "",
    ]
    check = "x" if item["state"] == "closed" else " "
    lines += [f"- [{check}] {x}" for x in item["acceptance"]]
    if item["kind"] == "epic":
        children = [x for x in plan["issues"] if x["parent"] == item["key"]]
        lines += [
            "",
            "## 拆分与推进",
            "",
            f"本组包含 {len(children)} 个新建叶子任务；使用下方 GitHub 原生 Sub-issues 认领和跟踪。",
            "先完成各子任务正文中的前置依赖，再按标签选择工作。父 Issue 不另计开发工作量。",
        ]
        if item["key"] == "EF0":
            lines += [
                "既有 #2（QQbot 建议）一并纳入本组；保留原作者内容，先讨论授权与可行性。"
            ]
    else:
        lines += [
            "",
            "## 依赖与认领",
            "",
            f"父 Issue：{reference(item['parent'], mapping)}。",
        ]
        lines += [
            "前置任务："
            + (
                "、".join(reference(d, mapping) for d in item["depends_on"])
                if item["depends_on"]
                else "无，可独立认领。"
            )
        ]
        size_text = {"XS": "半天以内", "S": "半天至一天", "M": "一至两天"}[item["size"]]
        lines += [
            f"预计规模：{item['size']}（{size_text}，包含必要验证；按认领者经验校准，超过两天继续拆分）。",
            "负责人待认领；填写 Assignee 后开始，建议同时最多推进一个叶子任务。",
        ]
    lines += ["", "## 依据与验证", "", item["source"] + "。"]
    paths = re.findall(
        r"(?:README\.md|AGENTS\.md|CONTRIBUTING\.md|docs/ARCHITECTURE\.md)",
        item["source"],
    )
    lines += [
        " · ".join(
            f"[{p}](https://github.com/{plan['repository']}/blob/main/{p})"
            for p in dict.fromkeys(paths)
        )
    ]
    if item["state"] == "closed":
        lines += [
            "",
            "**历史实现补录**：" + item["evidence"] + "。",
            "关闭依据是所列实现已合并且代码结构可核实，不表示当前环境已运行通过或所属 Milestone 已验收。",
            "PR #1 由 24151735 创建并合并，功能提交作者字段为 IVANLEE；这里不推断个人代码工作量。",
            "数据库迁移和接口复验分别由 W003、T003 跟踪。",
        ]
    elif item["kind"] != "epic":
        lines += [
            "",
            "提交时附与本项验收相匹配的命令结果、截图或文档链接；未运行的检查标记未验证。",
            "代码变更执行贡献指南规定的相关检查；纯文档或调研任务提交可审阅成果，无需无意义代码测试。",
            "关闭叶子任务前由其他成员核对验收证据；不要通过本项 PR 自动关闭整个父 Issue。",
        ]
    return "\n".join(lines).strip() + "\n"


def fetch_state(plan):
    base = "repos/" + plan["repository"]
    return {
        "labels": api(base + "/labels?per_page=100", paginate=True),
        "milestones": api(base + "/milestones?state=all&per_page=100", paginate=True),
        "issues": api(base + "/issues?state=all&per_page=100", paginate=True),
        "tags": api(base + "/tags?per_page=100", paginate=True),
    }


def map_issues(rows):
    out = {}
    for row in rows:
        match = MARKER.search(row.get("body") or "")
        if match:
            key = match.group(1)
            assert key not in out, f"Duplicate remote marker: {key}"
            out[key] = {
                "number": row["number"],
                "node_id": row["node_id"],
                "id": row["id"],
                "url": row["html_url"],
            }
    return out


def publish(plan, confirm):
    assert confirm == plan["repository"], (
        "--confirm-repo must exactly match manifest repository"
    )
    receipt = DIRECTORY / "audit.json"
    sealed = MAP.exists() and read(MAP).get("publication_complete")
    if sealed or (receipt.exists() and read(receipt).get("passed")):
        raise RuntimeError(
            "Initial publication already audited. Manage subsequent progress on GitHub; do not replay the snapshot."
        )
    ordered = validate(plan)
    base = "repos/" + plan["repository"]
    remote = fetch_state(plan)
    snapshot = DIRECTORY / "github-baseline.json"
    if not snapshot.exists():
        save(
            snapshot,
            {
                "repository": plan["repository"],
                "labels": [{"name": x["name"]} for x in remote["labels"]],
                "issues": [
                    {
                        k: x.get(k)
                        for k in (
                            "number",
                            "title",
                            "body",
                            "state",
                            "milestone",
                            "assignees",
                        )
                    }
                    for x in remote["issues"]
                ],
                "tags": remote["tags"],
            },
        )
    mapping = {
        "repository": plan["repository"],
        "milestones": {},
        "issues": map_issues(remote["issues"]),
    }
    repo = api(base)
    labels = {x["name"]: x for x in remote["labels"]}
    for desired in plan["labels"]:
        current = labels.get(desired["name"])
        if current is None:
            current = mutate(base + "/labels", desired)
        elif any(current.get(k) != desired[k] for k in ("color", "description")):
            current = mutate(
                base + "/labels/" + quote(desired["name"], safe=""), desired, "PATCH"
            )
        labels[desired["name"]] = current
    print(f"Labels ready: {len(plan['labels'])}", flush=True)
    milestones = {x["title"]: x for x in remote["milestones"]}
    for desired in plan["milestones"]:
        current = milestones.get(desired["title"])
        if current is None:
            current = mutate(
                base + "/milestones",
                {
                    "title": desired["title"],
                    "description": milestone_body(desired),
                    "state": "open",
                },
            )
        else:
            assert current["description"] == milestone_body(desired), (
                "Existing milestone differs; review manually"
            )
        mapping["milestones"][desired["key"]] = {
            k: current[k] for k in ("number", "node_id", "html_url")
        }
    save(MAP, mapping)
    print(f"Milestones ready: {len(mapping['milestones'])}", flush=True)
    # Parents first, then a topological ordering of actionable leaves.
    sequence = [x for x in ordered if x["kind"] == "epic"] + [
        x for x in ordered if x["kind"] != "epic"
    ]
    pending = [x for x in sequence if x["key"] not in mapping["issues"]]
    while pending:
        batch = [
            x
            for x in pending
            if all(d in mapping["issues"] for d in x["depends_on"])
            and (not x["parent"] or x["parent"] in mapping["issues"])
        ][:5]
        assert batch, "No publishable task; dependencies require inspection"
        variables = {}
        declarations, operations = [], []
        for index, item in enumerate(batch):
            alias = f"i{index}"
            payload = {
                "repositoryId": repo["node_id"],
                "title": f"[{item['key']}] {item['title']}",
                "body": issue_body(item, plan, mapping),
                "labelIds": [labels[n]["node_id"] for n in item["labels"]],
            }
            if item["milestone"]:
                payload["milestoneId"] = mapping["milestones"][item["milestone"]][
                    "node_id"
                ]
            if item["parent"]:
                payload["parentIssueId"] = mapping["issues"][item["parent"]]["node_id"]
            variables[alias] = payload
            declarations.append(f"${alias}:CreateIssueInput!")
            operations.append(
                f"{alias}:createIssue(input:${alias}){{issue{{id databaseId number url}}}}"
            )
        # Top-level GraphQL mutation fields execute serially. Batch only tasks
        # whose dependencies already exist, and throttle by item count.
        time.sleep(1.25 * (len(batch) - 1))
        query = "mutation(" + ",".join(declarations) + "){" + " ".join(operations) + "}"
        response = mutate("graphql", {"query": query, "variables": variables})["data"]
        for index, item in enumerate(batch):
            created = response[f"i{index}"]["issue"]
            mapping["issues"][item["key"]] = {
                "number": created["number"],
                "node_id": created["id"],
                "id": created["databaseId"],
                "url": created["url"],
            }
            save(MAP, mapping)
            pending.remove(item)
            print(
                f"{len(mapping['issues'])}/{len(sequence)} {item['key']} -> #{created['number']}",
                flush=True,
            )
    # Closing only historical implementation registrations; never close broad epics.
    fresh = api(base + "/issues?state=all&per_page=100", paginate=True)
    by_number = {x["number"]: x for x in fresh}
    for item in sequence:
        number = mapping["issues"][item["key"]]["number"]
        if item["state"] == "closed" and by_number[number]["state"] != "closed":
            mutate(
                base + f"/issues/{number}",
                {"state": "closed", "state_reason": "completed"},
                "PATCH",
            )
            print(f"Historical implementation recorded: #{number}", flush=True)
    for existing in plan.get("existing_issues", []):
        row = by_number[existing["number"]]
        union = sorted(set(existing["labels"]) | {x["name"] for x in row["labels"]})
        if union != sorted(x["name"] for x in row["labels"]):
            mutate(base + f"/issues/{existing['number']}", {"labels": union}, "PATCH")
        parent = mapping["issues"][existing["parent"]]
        subs = api(
            base + f"/issues/{parent['number']}/sub_issues?per_page=100", paginate=True
        )
        if existing["number"] not in [x["number"] for x in subs]:
            mutate(
                base + f"/issues/{parent['number']}/sub_issues",
                {"sub_issue_id": row["id"]},
            )
    render(plan, mapping)
    print("Publication finished; run audit for authoritative verification.", flush=True)


def render(plan, mapping):
    validate(plan)
    counts = Counter(x["kind"] == "epic" for x in plan["issues"])
    lines = [
        "# CampusFlow 开发任务索引",
        "",
        f"本次规划：{len(plan['milestones'])} 个 Milestone、{counts[True]} 个父 Issue、{counts[False]} 个叶子任务。另保留既有 QQbot 建议 #2。",
        "",
        "叶子任务以半天至两天为参考规模；超过两天继续拆分。规模是认领参考，不是交付日期承诺。",
        "GitHub 是发布后的任务状态与认领信息来源；本文件和 backlog.json 是初始范围快照，不用于覆盖后续状态。",
        "",
        "规划规则见 [ROADMAP.md](ROADMAP.md)，结构化清单见 [backlog.json](backlog.json)。",
        "",
    ]
    for ms in plan["milestones"] + [
        {"key": None, "title": "后续探索（无交付 Milestone）"}
    ]:
        lines += [f"## {ms['title']}", ""]
        for parent in [
            x
            for x in plan["issues"]
            if x["kind"] == "epic" and x["milestone"] == ms["key"]
        ]:
            mapped = mapping.get("issues", {}).get(parent["key"])
            label = f"[{parent['key']}] {parent['title']}"
            if mapped:
                label = f"[{label}]({mapped['url']})"
            lines += [
                f"### {label}",
                "",
                "| 任务 | 初始状态 | 规模 | 前置任务 |",
                "| --- | --- | --- | --- |",
            ]
            for item in [x for x in plan["issues"] if x["parent"] == parent["key"]]:
                m = mapping.get("issues", {}).get(item["key"])
                label = f"{item['key']} · {item['title']}"
                if m:
                    label = f"[{label}]({m['url']})"
                deps = (
                    ", ".join(reference(d, mapping) for d in item["depends_on"]) or "无"
                )
                lines += [
                    f"| {label} | {'已合并补录' if item['state'] == 'closed' else '待认领'} | {item['size']} | {deps} |"
                ]
            if parent["key"] == "EF0":
                lines += [
                    f"| [既有 #2：优化查询方式](https://github.com/{plan['repository']}/issues/2) | 待范围决策 | 待评估 | 不纳入前三阶段 |"
                ]
            lines += [""]
    (DIRECTORY / "ISSUES.md").write_text("\n".join(lines) + "\n")
    previews = Path("/private/tmp/campusflow-issue-bodies")
    previews.mkdir(exist_ok=True)
    for item in plan["issues"]:
        (previews / f"{item['key']}.md").write_text(issue_body(item, plan, mapping))


def audit(plan):
    validate(plan)
    mapping = read(MAP)
    remote = fetch_state(plan)
    by_key = {}
    errors = []
    for row in remote["issues"]:
        match = MARKER.search(row.get("body") or "")
        if match:
            key = match.group(1)
            if key in by_key:
                errors.append(f"Duplicate {key}")
            by_key[key] = row
    for item in plan["issues"]:
        row = by_key.get(item["key"])
        if not row:
            errors.append("Missing " + item["key"])
            continue
        expected_ms = (
            mapping["milestones"][item["milestone"]]["number"]
            if item["milestone"]
            else None
        )
        actual_ms = row["milestone"]["number"] if row["milestone"] else None
        checks = {
            "title": row["title"] == f"[{item['key']}] {item['title']}",
            "body": row["body"] == issue_body(item, plan, mapping),
            "labels": set(item["labels"]) <= {x["name"] for x in row["labels"]},
            "milestone": actual_ms == expected_ms,
            "state": row["state"] == item["state"],
            "assignees": not row["assignees"],
        }
        errors += [item["key"] + ": " + name for name, ok in checks.items() if not ok]
    # Read actual GitHub native parent relationships for every created issue.
    ids = [row["node_id"] for row in by_key.values()]
    parents = {}
    for start in range(0, len(ids), 50):
        result = api(
            "graphql",
            {
                "query": "query($ids:[ID!]!){nodes(ids:$ids){...on Issue{number parent{number} subIssues(first:100){nodes{number}}}}}",
                "variables": {"ids": ids[start : start + 50]},
            },
        )
        for node in result["data"]["nodes"]:
            parents[node["number"]] = node
    for item in plan["issues"]:
        if item["key"] not in by_key:
            continue
        actual = parents[by_key[item["key"]]["number"]]
        expected_parent = (
            mapping["issues"][item["parent"]]["number"] if item["parent"] else None
        )
        if (
            actual["parent"]["number"] if actual["parent"] else None
        ) != expected_parent:
            errors.append(item["key"] + ": native parent")
        if item["kind"] == "epic":
            expected = {
                mapping["issues"][x["key"]]["number"]
                for x in plan["issues"]
                if x["parent"] == item["key"]
            }
            expected.update(
                x["number"]
                for x in plan.get("existing_issues", [])
                if x["parent"] == item["key"]
            )
            if expected != {x["number"] for x in actual["subIssues"]["nodes"]}:
                errors.append(item["key"] + ": native children")
    labels = {x["name"]: x for x in remote["labels"]}
    for item in plan["labels"]:
        if not all(
            labels.get(item["name"], {}).get(k) == item[k]
            for k in ("color", "description")
        ):
            errors.append("label: " + item["name"])
    milestones = {x["title"]: x for x in remote["milestones"]}
    for item in plan["milestones"]:
        actual = milestones.get(item["title"], {})
        if (
            actual.get("description") != milestone_body(item)
            or actual.get("state") != "open"
            or actual.get("due_on")
        ):
            errors.append("milestone: " + item["key"])
    original = read(DIRECTORY / "github-baseline.json")
    originals = {x["number"]: x for x in original["issues"]}
    current = {x["number"]: x for x in remote["issues"]}
    for item in plan.get("existing_issues", []):
        old, new = originals[item["number"]], current[item["number"]]
        if any(
            old.get(k) != new.get(k)
            for k in ("title", "body", "state", "milestone", "assignees")
        ):
            errors.append("Existing issue content changed")
        if not set(item["labels"]) <= {x["name"] for x in new["labels"]}:
            errors.append("Existing issue labels missing")
    if remote["tags"] != original["tags"]:
        errors.append("Git version tags changed")
    if not {x["name"] for x in original["labels"]} <= set(labels):
        errors.append("Existing labels removed")
    unexpected = set(by_key) - {x["key"] for x in plan["issues"]}
    errors += ["Unexpected managed key " + x for x in unexpected]
    report = {
        "repository": plan["repository"],
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "milestones": len(plan["milestones"]),
        "managed_labels": len(plan["labels"]),
        "new_issues": len(plan["issues"]),
        "epics": sum(x["kind"] == "epic" for x in plan["issues"]),
        "leaf_tasks": sum(x["kind"] != "epic" for x in plan["issues"]),
        "closed_historical": sum(x["state"] == "closed" for x in plan["issues"]),
        "existing_issues_preserved": [
            x["number"] for x in plan.get("existing_issues", [])
        ],
        "native_parent_relationships_checked": len(parents),
        "version_tags_unchanged": remote["tags"] == original["tags"],
        "errors": errors,
        "passed": not errors,
    }
    save(DIRECTORY / "audit.json", report)
    if not errors:
        mapping["publication_complete"] = True
        save(MAP, mapping)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate", "render", "publish", "audit"])
    parser.add_argument("--confirm-repo")
    args = parser.parse_args()
    plan = read(PLAN)
    validate(plan)
    if args.action == "publish":
        publish(plan, args.confirm_repo)
    elif args.action == "audit":
        audit(plan)
    elif args.action == "render":
        render(plan, read(MAP) if MAP.exists() else {})
    else:
        print(f"Valid: {len(plan['issues'])} issues; dependency graph has no cycles")


if __name__ == "__main__":
    main()
