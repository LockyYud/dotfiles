#!/home/do-duy/.config/eww/AI/.venv/bin/python
"""
Personal AI assistant backend for eww / bubbly.

Features
--------
- Chat with an OpenAI model.
- Manage tasks: JSON (default) or Notion database.
- Render chat history and tasks as yuck snippets for eww widgets.
- Optional Notion integration: tasks + search/get page as context.

Environment
-----------
- OPENAI_API_KEY: required.
- AI_MODEL: optional, defaults to "gpt-4.1-mini".
- NOTION_API_KEY: optional, for Notion integration.
- NOTION_TASK_DATABASE_ID: optional, database ID for tasks (share with integration).
- NOTION_TITLE_PROP, NOTION_DONE_PROP: optional, property names (default: Name, Done).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

try:
    from openai import OpenAI
except Exception as exc:  # pragma: no cover - import error path
    sys.stderr.write(
        "[assistant.py] Failed to import openai. Install with `pip install openai`.\n"
    )
    raise

try:
    from mem0 import MemoryClient
except Exception:
    MemoryClient = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CONV_FILE = DATA_DIR / "conversation.json"
STATUS_FILE = DATA_DIR / "status"
TASKS_FILE = ROOT / "tasks.json"
BB_HISTORY_CACHE = DATA_DIR / "bubbly_history_cache.yuck"

MODEL = os.environ.get("AI_MODEL", "gpt-5-mini")


def _create_client() -> OpenAI:
    """
    Create an OpenAI client.

    Priority:
    1. OPENAI_API_KEY from environment.
    2. Key file at ~/.config/eww/AI/openai_api_key (first line).
    3. Fallback to default client configuration.
    """
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        key_path = ROOT / "openai_api_key"
        try:
            key = key_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            key = None
    if key:
        return OpenAI(api_key=key)
    return OpenAI()


client = _create_client()


def _read_key_file(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None


def _create_mem0_client() -> Optional["MemoryClient"]:
    """
    Create a Mem0 MemoryClient using MEM0_API_KEY or mem0_api_key file.
    Returns None if mem0 is not installed or not configured.
    """
    if MemoryClient is None:
        return None
    api_key = os.environ.get("MEM0_API_KEY") or _read_key_file(ROOT / "mem0_api_key")
    if not api_key:
        return None
    try:
        return MemoryClient(api_key=api_key)
    except Exception:
        return None


MEM0_CLIENT = _create_mem0_client()
MEM0_USER_ID = (
    os.environ.get("MEM0_USER_ID")
    or os.environ.get("USER")
    or os.environ.get("USERNAME")
    or "local-user"
)


# --- Notion config ---
NOTION_API_KEY = os.environ.get("NOTION_API_KEY") or _read_key_file(
    ROOT / "notion_api_key"
)
NOTION_DB_ID = os.environ.get("NOTION_TASK_DATABASE_ID") or _read_key_file(
    ROOT / "notion_task_database_id"
)
NOTION_TITLE_PROP = os.environ.get("NOTION_TITLE_PROP", "Name")
NOTION_DONE_PROP = os.environ.get("NOTION_DONE_PROP", "Done")
NOTION_DESC_PROP = os.environ.get("NOTION_DESC_PROP", "Description")
NOTION_DATE_PROP = os.environ.get("NOTION_DATE_PROP", "Date")

# --- Tavily config ---
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY") or _read_key_file(
    ROOT / "tavily_api_key"
)


def _notion_request(
    method: str, path: str, data: Optional[Dict] = None
) -> Dict[str, Any]:
    if not NOTION_API_KEY:
        return {"error": "Notion API key not configured"}
    url = f"https://api.notion.com/v1{path}"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": "2022-02-22",
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode("utf-8") if data and method != "GET" else None
    req = urllib_request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib_request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8"))
            return {"error": err_body.get("message", str(e))}
        except Exception:
            return {"error": str(e)}
    except (URLError, TimeoutError) as e:
        return {"error": str(e)}


def _use_notion_tasks() -> bool:
    return bool(NOTION_API_KEY and NOTION_DB_ID)


_NOTION_TASK_PROPS_ENSURED = False


def _notion_ensure_task_properties() -> None:
    """
    Ensure the Notion task database has the properties we expect:
    - NOTION_TITLE_PROP (title)
    - NOTION_DONE_PROP (checkbox)
    - NOTION_DESC_PROP (rich_text)
    - NOTION_DATE_PROP (date)
    """
    global _NOTION_TASK_PROPS_ENSURED

    if _NOTION_TASK_PROPS_ENSURED or not _use_notion_tasks():
        return

    db_id = (NOTION_DB_ID or "").replace("-", "")
    if not db_id:
        return

    # Fetch current database properties
    resp = _notion_request("GET", f"/databases/{db_id}")
    if "error" in resp:
        # Fail silently – we'll just skip ensuring properties
        return

    props = resp.get("properties", {}) or {}
    update: Dict[str, Any] = {"properties": {}}

    # Only add missing props; never modify existing ones.
    if NOTION_DESC_PROP and NOTION_DESC_PROP not in props:
        update["properties"][NOTION_DESC_PROP] = {"rich_text": {}}
    if NOTION_DATE_PROP and NOTION_DATE_PROP not in props:
        update["properties"][NOTION_DATE_PROP] = {"date": {}}

    if update["properties"]:
        _notion_request("PATCH", f"/databases/{db_id}", update)

    _NOTION_TASK_PROPS_ENSURED = True


def _notion_parse_page(page: Dict) -> Dict[str, Any]:
    props = page.get("properties", {})
    title_prop = props.get(NOTION_TITLE_PROP, {})
    title_arr = title_prop.get("title", [])
    title = title_arr[0].get("plain_text", "") if title_arr else ""
    done_prop = props.get(NOTION_DONE_PROP, {})
    done = bool(done_prop.get("checkbox", False))

    # Optional description (rich_text)
    description = ""
    desc_prop = props.get(NOTION_DESC_PROP, {})
    if isinstance(desc_prop, Dict) and desc_prop.get("type") == "rich_text":
        rt = desc_prop.get("rich_text", [])
        if isinstance(rt, list):
            description = "".join(part.get("plain_text", "") for part in rt)

    # Optional date property used as "due"
    due: Optional[str] = None
    date_prop = props.get(NOTION_DATE_PROP, {})
    if isinstance(date_prop, Dict) and date_prop.get("type") == "date":
        date_dict = date_prop.get("date") or {}
        if isinstance(date_dict, Dict):
            val = date_dict.get("start") or ""
            if val:
                due = str(val)

    created_at = str(page.get("created_time", "")) if page.get("created_time") else ""

    return {
        "id": page.get("id", ""),
        "title": title,
        "description": description,
        "done": done,
        "created_at": created_at,
        "due": due,
    }


def _notion_query_tasks() -> List[Dict[str, Any]]:
    if not _use_notion_tasks():
        return []
    _notion_ensure_task_properties()
    db_id = (NOTION_DB_ID or "").replace("-", "")
    if not db_id:
        return []
    resp = _notion_request("POST", f"/databases/{db_id}/query", {})
    if "error" in resp:
        return []
    results = resp.get("results", [])
    return [_notion_parse_page(p) for p in results]


def _notion_add_task(title: str, due: Optional[str] = None) -> Dict[str, Any]:
    if not _use_notion_tasks():
        return {"error": "Notion not configured"}
    _notion_ensure_task_properties()
    db_id = (NOTION_DB_ID or "").replace("-", "")
    body = {
        "parent": {"database_id": db_id},
        "properties": {
            NOTION_TITLE_PROP: {"title": [{"text": {"content": title.strip()}}]},
            NOTION_DONE_PROP: {"checkbox": False},
        },
    }
    if due:
        body["properties"][NOTION_DATE_PROP] = {
            "date": {
                "start": due.strip(),
            }
        }
    resp = _notion_request("POST", "/pages", body)
    if "error" in resp:
        return resp
    return _notion_parse_page(resp)


def _notion_toggle_task(task_id: str) -> Dict[str, Any]:
    if not _use_notion_tasks():
        return {"error": "Notion not configured"}
    tasks = _notion_query_tasks()
    for t in tasks:
        tid = (t.get("id") or "").replace("-", "")
        if tid == (task_id or "").replace("-", ""):
            new_done = not t.get("done", False)
            page_id = (t.get("id") or "").replace("-", "")
            _notion_request(
                "PATCH",
                f"/pages/{page_id}",
                {"properties": {NOTION_DONE_PROP: {"checkbox": new_done}}},
            )
            t["done"] = new_done
            return t
    return {"error": f"task {task_id} not found"}


def _notion_clear_done() -> Dict[str, Any]:
    if not _use_notion_tasks():
        return {"error": "Notion not configured"}
    tasks = _notion_query_tasks()
    done_ids = [t["id"] for t in tasks if t.get("done")]
    for pid in done_ids:
        _notion_request("PATCH", f"/pages/{pid.replace('-', '')}", {"archived": True})
    return {"removed": len(done_ids), "remaining": len(tasks) - len(done_ids)}


def _notion_delete_task(task_id: str) -> Dict[str, Any]:
    """
    Archive (soft-delete) a single Notion task by id.
    """
    if not _use_notion_tasks():
        return {"error": "Notion not configured"}
    tasks = _notion_query_tasks()
    for t in tasks:
        tid = (t.get("id") or "").replace("-", "")
        if tid == (task_id or "").replace("-", ""):
            page_id = tid
            _notion_request("PATCH", f"/pages/{page_id}", {"archived": True})
            return {"removed": 1, "id": t.get("id", "")}
    return {"error": f"task {task_id} not found"}


def _notion_search(query: str, limit: int = 5) -> Dict[str, Any]:
    if not NOTION_API_KEY:
        return {"error": "Notion API key not configured"}
    body = {"query": query, "page_size": limit}
    resp = _notion_request("POST", "/search", body)
    if "error" in resp:
        return resp
    results = resp.get("results", [])
    snippets = []
    for r in results:
        obj_type = r.get("object", "")
        pid = r.get("id", "")
        title = ""
        if "title" in r:
            arr = r.get("title", [])
            title = arr[0].get("plain_text", "") if arr else ""
        props = r.get("properties", {})
        for k, v in props.items():
            if v.get("type") == "title":
                arr = v.get("title", [])
                title = arr[0].get("plain_text", "") if arr else title or k
                break
        snippets.append({"id": pid, "type": obj_type, "title": title})
    return {"results": snippets}


def _notion_get_page(page_id: str) -> Dict[str, Any]:
    if not NOTION_API_KEY:
        return {"error": "Notion API key not configured"}
    pid = page_id.replace("-", "")
    resp = _notion_request("GET", f"/pages/{pid}")
    if "error" in resp:
        return resp
    blocks_resp = _notion_request("GET", f"/blocks/{pid}/children")
    blocks = blocks_resp.get("results", []) if "error" not in blocks_resp else []
    text_parts = []
    for b in blocks:
        bt = b.get("type", "")
        content = b.get(bt, {})
        if bt == "paragraph":
            arr = content.get("rich_text", [])
            text_parts.append("".join(x.get("plain_text", "") for x in arr))
        elif bt == "heading_1":
            arr = content.get("rich_text", [])
            text_parts.append("# " + "".join(x.get("plain_text", "") for x in arr))
        elif bt == "heading_2":
            arr = content.get("rich_text", [])
            text_parts.append("## " + "".join(x.get("plain_text", "") for x in arr))
        elif bt == "bulleted_list_item":
            arr = content.get("rich_text", [])
            text_parts.append("- " + "".join(x.get("plain_text", "") for x in arr))
        elif bt == "to_do":
            arr = content.get("rich_text", [])
            checked = content.get("checked", False)
            text_parts.append(
                ("[x] " if checked else "[ ] ")
                + "".join(x.get("plain_text", "") for x in arr)
            )
    return {"content": "\n".join(text_parts), "id": resp.get("id", "")}


def tavily_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    """
    Search the web using Tavily API.
    Returns a list of search results with title, url, and content snippet.
    """
    if not TAVILY_API_KEY:
        return {"error": "Tavily API key not configured"}

    url = "https://api.tavily.com/search"
    data = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "include_answer": True,
        "include_raw_content": False,
    }

    body = json.dumps(data).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    req = urllib_request.Request(url, data=body, headers=headers, method="POST")

    try:
        with urllib_request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        results = []
        for r in result.get("results", []):
            results.append(
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                    "score": r.get("score", 0),
                }
            )

        return {
            "answer": result.get("answer", ""),
            "results": results,
            "query": query,
        }
    except HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8"))
            return {"error": err_body.get("message", str(e))}
        except Exception:
            return {"error": str(e)}
    except (URLError, TimeoutError) as e:
        return {"error": str(e)}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TASKS_FILE.exists():
        _save_tasks({"tasks": []})


def _load_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _load_history() -> List[Dict[str, Any]]:
    data = _load_json(CONV_FILE, {"messages": []})
    messages = data.get("messages") or []
    if not isinstance(messages, list):
        return []
    return messages


def _save_history(messages: List[Dict[str, Any]]) -> None:
    _save_json(CONV_FILE, {"messages": messages})


def _write_bubbly_history_cache(messages: List[Dict[str, Any]]) -> None:
    """
    Cache bubbly yuck labels for chat history (excluding the typing bubble).
    This avoids re-parsing and re-formatting the full history on every poll.
    """
    chat_msgs: List[Dict[str, Any]] = []
    for m in messages:
        if m.get("role") in {"user", "assistant"} and (m.get("content") is not None):
            chat_msgs.append(m)
    chat_msgs = chat_msgs[-10:]

    def _label(text: str, is_assistant: bool) -> str:
        raw = text
        width = min(530, max(160, len(raw) * 10 + 40))
        txt = raw.replace("\\", "\\\\").replace("'", "\\'")
        role_class = "label-assistant" if is_assistant else "label-user"
        return (
            f"(label :class 'label {role_class}' :text '{txt}' :wrap true :width '{width}' "
            f":xalign 0 :halign 'start')"
        )

    labels: List[str] = []
    if not chat_msgs:
        labels.append(_label("AI chat ready. Type and press Enter.", True))
    else:
        for m in chat_msgs:
            role = m.get("role")
            content = str(m.get("content") or "")
            if role == "user":
                labels.append(_label(content, False))
            elif role == "assistant":
                labels.append(_label(content, True))

    BB_HISTORY_CACHE.parent.mkdir(parents=True, exist_ok=True)
    BB_HISTORY_CACHE.write_text(" ".join(labels), encoding="utf-8")


def _read_status() -> str:
    try:
        return STATUS_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _write_status(text: str) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(text, encoding="utf-8")


@dataclass
class Task:
    id: str
    title: str
    done: bool = False
    created_at: str = ""
    due: Optional[str] = None


def _load_tasks() -> Dict[str, Any]:
    data = _load_json(TASKS_FILE, {"tasks": []})
    if "tasks" not in data or not isinstance(data["tasks"], list):
        data = {"tasks": []}
    return data


def _save_tasks(data: Dict[str, Any]) -> None:
    _save_json(TASKS_FILE, data)


def task_add(title: str, due: Optional[str] = None) -> Dict[str, Any]:
    if _use_notion_tasks():
        return _notion_add_task(title, due)
    store = _load_tasks()
    task = Task(
        id=str(uuid.uuid4()),
        title=title.strip(),
        done=False,
        created_at=_now_iso(),
        due=due.strip() if due else None,
    )
    store["tasks"].append(asdict(task))
    _save_tasks(store)
    return task.__dict__


def task_toggle(task_id: str) -> Dict[str, Any]:
    if _use_notion_tasks():
        return _notion_toggle_task(task_id)
    store = _load_tasks()
    for t in store["tasks"]:
        if t.get("id") == task_id:
            t["done"] = not bool(t.get("done"))
            _save_tasks(store)
            return t
    return {"error": f"task {task_id} not found"}


def task_clear_done() -> Dict[str, Any]:
    if _use_notion_tasks():
        return _notion_clear_done()
    store = _load_tasks()
    before = len(store["tasks"])
    store["tasks"] = [t for t in store["tasks"] if not t.get("done")]
    after = len(store["tasks"])
    _save_tasks(store)
    return {"removed": before - after, "remaining": after}


def task_delete(task_id: str) -> Dict[str, Any]:
    """
    Delete a single task by id (or archive it in Notion).
    """
    if _use_notion_tasks():
        return _notion_delete_task(task_id)
    store = _load_tasks()
    before = len(store["tasks"])
    store["tasks"] = [t for t in store["tasks"] if t.get("id") != task_id]
    after = len(store["tasks"])
    _save_tasks(store)
    if before == after:
        return {"error": f"task {task_id} not found"}
    return {"removed": 1, "remaining": after}


def task_list() -> Dict[str, Any]:
    if _use_notion_tasks():
        return {"tasks": _notion_query_tasks()}
    store = _load_tasks()
    return {"tasks": store["tasks"]}


def get_datetime() -> Dict[str, str]:
    return {"now": _now_iso()}


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "task_add",
            "description": "Add a new personal task to the task list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short task title, in the user's language.",
                    },
                    "due": {
                        "type": "string",
                        "description": "Optional due date/time in natural language or ISO8601.",
                    },
                },
                "required": ["title"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_toggle",
            "description": "Toggle completion state of a task by its id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The id of the task to toggle.",
                    }
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_delete",
            "description": "Delete a task by its id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The id of the task to delete.",
                    }
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_list",
            "description": "List all tasks with their status.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_clear_done",
            "description": "Remove all completed tasks from the list.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_datetime",
            "description": "Get the current local date and time.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notion_search",
            "description": "Search the user's Notion workspace for pages and databases matching a query. Use when the user asks about notes, docs, or info that might be in Notion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (keywords, topic, etc.).",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notion_get_page",
            "description": "Get the text content of a Notion page by its ID. Use after notion_search to read a specific page's content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "The Notion page ID (from notion_search results).",
                    },
                },
                "required": ["page_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tavily_search",
            "description": "Search the web using Tavily API. Use when the user asks about current events, recent information, facts, or anything that requires internet search. Returns relevant web results with summaries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query in natural language.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default: 5).",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
]


def _dispatch_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if name == "task_add":
        return {
            "tool": name,
            "output": task_add(arguments["title"], arguments.get("due")),
        }
    if name == "task_toggle":
        return {"tool": name, "output": task_toggle(arguments["task_id"])}
    if name == "task_delete":
        return {"tool": name, "output": task_delete(arguments["task_id"])}
    if name == "task_list":
        return {"tool": name, "output": task_list()}
    if name == "task_clear_done":
        return {"tool": name, "output": task_clear_done()}
    if name == "get_datetime":
        return {"tool": name, "output": get_datetime()}
    if name == "notion_search":
        return {"tool": name, "output": _notion_search(arguments.get("query", ""))}
    if name == "notion_get_page":
        return {"tool": name, "output": _notion_get_page(arguments.get("page_id", ""))}
    if name == "tavily_search":
        return {
            "tool": name,
            "output": tavily_search(
                arguments.get("query", ""),
                arguments.get("max_results", 5),
            ),
        }
    return {"tool": name, "error": "unknown tool"}


def _mem0_search_context(query: str) -> str:
    """
    Fetch relevant long-term memories from Mem0 for the current query.
    Returns a formatted string suitable for a system message, or empty string
    if Mem0 is not configured or no memories are found.
    """
    if not query or MEM0_CLIENT is None:
        return ""
    try:
        res = MEM0_CLIENT.search(query, filters={"user_id": MEM0_USER_ID})
    except Exception:
        return ""

    if isinstance(res, dict):
        items = res.get("results") or []
    else:
        items = res or []

    memories: List[str] = []
    for item in items:
        mem_text = ""
        if isinstance(item, Dict):
            mem_text = str(item.get("memory") or item.get("content") or "")
        else:
            mem_text = str(item)
        mem_text = mem_text.strip()
        if not mem_text:
            continue
        memories.append(f"- {mem_text}")
        if len(memories) >= 5:
            break

    if not memories:
        return ""

    return "Long-term memories about the user:\n" + "\n".join(memories)


def _mem0_add_turn(user_text: str, assistant_text: str) -> None:
    """
    Store the latest user/assistant turn in Mem0 for long-term memory.
    """
    if MEM0_CLIENT is None:
        return
    user_text = (user_text or "").strip()
    assistant_text = (assistant_text or "").strip()
    messages: List[Dict[str, str]] = []
    if user_text:
        messages.append({"role": "user", "content": user_text})
    if assistant_text:
        messages.append({"role": "assistant", "content": assistant_text})
    if not messages:
        return
    try:
        MEM0_CLIENT.add(messages, user_id=MEM0_USER_ID)
    except Exception:
        # Mem0 failures should never break the main assistant flow.
        return


def chat_with_model(user_message: str) -> str:
    """
    Run a chat turn with optional tool calls and return the assistant reply text.
    Conversation history is persisted in CONV_FILE.
    """

    _ensure_dirs()
    mem0_context = _mem0_search_context(user_message)
    # Persist the user message immediately so the UI can show it right away,
    # even while we are still waiting on the model response.
    history_now: List[Dict[str, Any]] = _load_history()
    history_now.append({"role": "user", "content": user_message})
    _save_history(history_now)
    _write_bubbly_history_cache(history_now)

    messages: List[Dict[str, Any]] = history_now[:]
    notion_hint = ""
    if NOTION_API_KEY:
        notion_hint = " You have access to the user's Notion workspace. Use notion_search to find relevant pages when they ask about notes, docs, or stored information. Use notion_get_page to read a page's content."
    tavily_hint = ""
    if TAVILY_API_KEY:
        tavily_hint = " You can search the web using tavily_search when the user asks about current events, recent information, facts, or anything requiring internet lookup."
    mem0_hint = ""
    if MEM0_CLIENT is not None:
        mem0_hint = (
            " You have access to a long-term memory store about the user. "
            "Use any provided long-term memories to keep responses consistent and personalized over time."
        )
    messages.append(
        {
            "role": "system",
            "content": (
                "You are a personal productivity assistant integrated into a Linux desktop. "
                "You help the user manage personal tasks and planning. "
                "Prefer using the available tools to create, update, and list tasks instead of inventing them."
                + notion_hint
                + tavily_hint
                + mem0_hint
            ),
        }
    )
    if mem0_context:
        messages.append(
            {
                "role": "system",
                "content": mem0_context,
            }
        )

    while True:
        completion = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        message = completion.choices[0].message

        # If the model decided to call tools, execute them and loop.
        if message.tool_calls:
            messages.append(message.model_dump())
            for tool_call in message.tool_calls:
                name = tool_call.function.name
                try:
                    arguments = json.loads(tool_call.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                result = _dispatch_tool(name, arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            continue

        # Final assistant answer
        reply_text = message.content or ""
        messages.append({"role": "assistant", "content": reply_text})
        # Store only non-system messages in history for future turns.
        history = [m for m in messages if m.get("role") != "system"]
        _save_history(history)
        _write_bubbly_history_cache(history)
        _mem0_add_turn(user_message, reply_text)
        return reply_text


def _escape_yuck(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace('"', '\\"')
    text = text.replace("\n", " ")
    return text


def _read_bubbly_typing() -> str:
    """
    Read the current bubbly typing buffer (last /tmp/xkbN) and append a cursor.
    """
    try:
        src = Path("/tmp/bubble_count").read_text(encoding="utf-8")
        # src is like: export bubble_count=1
        n = int(src.strip().split("=", 1)[1])
    except Exception:
        n = 1
    path = Path(f"/tmp/xkb{n}")
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        content = ""
    content = content.strip("\n")
    if not content:
        return ""
    return content + " _"


def render_bubbly_yuck() -> None:
    """
    Print yuck in bubbly's expected style:
    - Past messages from conversation.json (user + assistant)
    - Current typing bubble at the bottom (from /tmp/xkbN)
    """
    # Read cached history labels if available (fast path).
    try:
        history_labels = BB_HISTORY_CACHE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        messages = _load_history()
        _write_bubbly_history_cache(messages)
        try:
            history_labels = BB_HISTORY_CACHE.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            history_labels = ""

    # typing bubble is dynamic (changes every keypress)
    typing = _read_bubbly_typing()
    if typing:
        raw = typing
        width = min(530, max(160, len(raw) * 10 + 40))
        txt = raw.replace("\\", "\\\\").replace("'", "\\'")
        typing_label = (
            f"(label :class 'label' :text '{txt}' :wrap true :width '{width}' "
            f":xalign 0 :halign 'start' :style '')"
        )
        inner = (history_labels + " " + typing_label).strip()
    else:
        inner = history_labels

    print(f"(box :orientation 'v' :space-evenly false :class 'chats' {inner} )")


def render_history_yuck() -> None:
    """
    Print a yuck snippet representing the chat history as bubbles.
    Uses the same label styles as bubbly (label-user / label-assistant).
    """

    messages = _load_history()
    _write_bubbly_history_cache(messages)
    try:
        labels = BB_HISTORY_CACHE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        labels = ""
    if not labels:
        labels = "(label :class 'label label-assistant' :text 'AI chat ready. Type and press Enter.' :wrap true :width '260' :xalign 0 :halign 'start')"

    # Append loading bubble if model is currently thinking
    status = _read_status()
    if "thinking" in status:
        loading_bubble = "(label :class 'label label-assistant label-loading' :text '● ● ●' :xalign 0 :halign 'start')"
        labels = labels + " " + loading_bubble

    box = f"(box :orientation 'v' :space-evenly false :class 'chats' {labels} )"
    print(box)


def render_tasks_yuck() -> None:
    data = task_list()
    rows: List[str] = []
    for t in data.get("tasks", []):
        task_id = t.get("id", "")
        title = _escape_yuck(str(t.get("title", "")))
        desc = _escape_yuck(str(t.get("description", "")))
        done = bool(t.get("done"))
        icon = "󰄲" if done else "󰄱"
        row = (
            '(box :orientation "h" :class "ai-task-row" :spacing 8 '
            f'(label :class "ai-task-title{" ai-task-title-done" if done else ""}" '
            f':xalign 0 :halign "start" :wrap true :hexpand "true" :text "{title}") '
            f'(label :class "ai-task-desc" :xalign 0 :halign "start" :wrap true :hexpand "true" :text "{desc}") '
            f'(button :class "ai-task-toggle" :onclick "~/.config/eww/AI/scripts/toggle-task.sh {task_id}" "{icon}") '
            f'(label :class "ai-task-date" :xalign 1 :halign "end" :text "{_escape_yuck(str(t.get("created_at", ""))[:10])}")'
            ")"
        )
        rows.append(row)

    header = (
        '(box :orientation "h" :class "ai-task-header" :spacing 8 '
        '(label :class "ai-task-header-label ai-task-header-name" :xalign 0 :halign "start" :text "Name") '
        '(label :class "ai-task-header-label ai-task-header-desc" :xalign 0 :halign "start" :text "Description") '
        '(label :class "ai-task-header-label ai-task-header-done" :xalign 0 :halign "start" :text "Done") '
        '(label :class "ai-task-header-label ai-task-header-date" :xalign 1 :halign "end" :text "Date")'
        ")"
    )

    body = (
        " ".join(rows)
        if rows
        else '(label :class "ai-task-empty" :text "No tasks yet.")'
    )
    inner = f"{header} {body}"
    box = f'(box :orientation "v" :space-evenly false :class "ai-tasks" {inner})'
    print(box)


HELP_TEXT = """Commands:
/clear — Xóa lịch sử chat.
/exit — Đóng cửa sổ chat.
/help — Hiển thị danh sách lệnh này."""


def clear_history() -> None:
    _save_history([])
    _write_bubbly_history_cache([])


def inject_help() -> None:
    """Append the help message as an assistant message so it appears in the chat UI."""
    messages = _load_history()
    messages.append({"role": "assistant", "content": HELP_TEXT})
    _save_history(messages)
    _write_bubbly_history_cache(messages)


def main(argv: Optional[List[str]] = None) -> int:
    _ensure_dirs()

    parser = argparse.ArgumentParser(
        prog="assistant.py", description="eww AI assistant backend"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    chat_p = sub.add_parser("chat", help="Send a chat message to the assistant")
    chat_p.add_argument(
        "message", nargs="?", help="Message text (reads stdin if omitted)"
    )

    sub.add_parser("render_history", help="Render chat history as yuck")
    sub.add_parser("render_bubbly", help="Render bubbly-style chat (history + typing)")
    sub.add_parser("render_tasks", help="Render tasks list as yuck")
    sub.add_parser("render_status", help="Print current status string")

    add_p = sub.add_parser("task_add", help="Add a new task")
    add_p.add_argument("title", help="Task title")
    add_p.add_argument("--due", help="Optional due date/time", default=None)

    toggle_p = sub.add_parser("task_toggle", help="Toggle task completion by id")
    toggle_p.add_argument("task_id", help="Task id")

    delete_p = sub.add_parser("task_delete", help="Delete a task by id")
    delete_p.add_argument("task_id", help="Task id")

    sub.add_parser("task_clear_done", help="Clear all completed tasks")
    sub.add_parser("task_list", help="Print raw tasks JSON")
    sub.add_parser("clear_history", help="Clear chat history")
    sub.add_parser("inject_help", help="Append /help message to chat (for UI)")
    sub.add_parser("ping", help="Healthcheck")

    args = parser.parse_args(argv)

    if args.cmd == "chat":
        text = args.message
        if not text:
            text = sys.stdin.read().strip()
        if not text:
            return 0
        _write_status("● thinking...")
        try:
            reply = chat_with_model(text)
        finally:
            _write_status("")
        print(reply)
        return 0

    if args.cmd == "render_history":
        render_history_yuck()
        return 0

    if args.cmd == "render_bubbly":
        render_bubbly_yuck()
        return 0

    if args.cmd == "render_tasks":
        render_tasks_yuck()
        return 0

    if args.cmd == "render_status":
        print(_read_status())
        return 0

    if args.cmd == "task_add":
        task = task_add(args.title, args.due)
        print(json.dumps(task, ensure_ascii=False))
        return 0

    if args.cmd == "task_toggle":
        result = task_toggle(args.task_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.cmd == "task_delete":
        result = task_delete(args.task_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.cmd == "task_clear_done":
        result = task_clear_done()
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.cmd == "task_list":
        print(json.dumps(task_list(), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "clear_history":
        clear_history()
        return 0

    if args.cmd == "inject_help":
        inject_help()
        return 0

    if args.cmd == "ping":
        print("ok")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
