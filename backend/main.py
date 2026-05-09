from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Dict, List, Optional
import uuid
import threading
import time
import os
from dotenv import load_dotenv
import traceback   # 在文件开头导入
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入你的游戏生成模块
from agent_deepseek import generate_opening, generate_next_story

# 获取当前文件所在目录（backend/）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 项目根目录（backend/ 的上一级）
ROOT_DIR = os.path.dirname(BASE_DIR)

load_dotenv()

app = FastAPI()

# 挂载前端静态文件（注意路径：../frontend）
app.mount("/assets", StaticFiles(directory=os.path.join(ROOT_DIR, "frontend", "assets")), name="assets")


# 存储会话数据
sessions: Dict[str, dict] = {}  # session_id -> {"session": GameSession, "progress": int, "status": str}
tasks: Dict[str, threading.Thread] = {}

class GameSession:
    def __init__(self, prompt):
        self.prompt = prompt
        self.history = []
        self.stats = {}
        self.inventory = []
        self.branch_cache = {} 
        self._init_game()
        self._pregenerate_branches()

    def _init_game(self):
        data = generate_opening(self.prompt)
        self.stats = data.get("stats", {"生命值": 100})
        self.inventory = data.get("inventory", [])
        description = data["description"]
        options = data["options"]
        self.history = [(description, options,self.stats.copy(), self.inventory.copy())]
        self.current_options = options

    def _pregenerate_branches(self):
        """为当前界面的每个选项预生成后续内容，存入缓存"""
        def generate_for_option(option_text):
            try:
                # 构建摘要（使用当前历史）
                summary = ""
                for i, (desc, opts, stats, inv) in enumerate(self.history[-3:]):
                    opt_texts = [opt["text"] for opt in opts]
                    summary += f"轮次{i+1}: {desc}\n选项: {', '.join(opt_texts)}\n属性: {stats}\n物品: {inv}\n\n"
                # 调用生成函数（注意：这里的 generate_next_story 需要支持 expected_effect，但预生成时没有 effect）
                data = generate_next_story(
                    user_choice=option_text,
                    history_summary=summary,
                    resources={"stats": self.stats, "inventory": self.inventory},
                    expected_effect=None  # 预生成时不需要效果提示
                )
                # 缓存结果（只需要保存必要的数据，实际处理变化时要复制当前状态）
                # 注意：由于变化需要基于当前状态，我们还需要在 take_action 时真正应用变化
                # 这里只缓存原始 AI 输出，避免重复调用 API
                self.branch_cache[option_text] = data
            except Exception as e:
                print(f"预生成选项 '{option_text}' 失败: {e}")
                self.branch_cache[option_text] = None

        # 启动线程为每个选项生成（不阻塞主流程）
        for opt in self.current_options:
            text = opt["text"]
            thread = threading.Thread(target=generate_for_option, args=(text,))
            thread.daemon = True
            thread.start()    

    def take_action(self, option_text, expected_effect=None):
        # 先从缓存中获取预生成的数据
        cached = self.branch_cache.get(option_text)
        if cached and cached is not None:
            data = cached
            # 从缓存中移除（可选），节省内存
            # del self.branch_cache[option_text]
        else:
            # 缓存未命中（可能预生成失败或未完成），则实时生成
            print(f"缓存未命中，实时生成选项 '{option_text}'")
            last_desc, last_opts, last_stats, last_inv = self.history[-1]
            summary = ""
            for i, (desc, opts, stats, inv) in enumerate(self.history[-3:]):
                opt_texts = [opt["text"] for opt in opts]
                summary += f"轮次{i+1}: {desc}\n选项: {', '.join(opt_texts)}\n属性: {stats}\n物品: {inv}\n\n"
            data = generate_next_story(
                user_choice=option_text,
                history_summary=summary,
                resources={"stats": self.stats, "inventory": self.inventory},
                expected_effect=expected_effect
            )
        new_desc = data["description"]
        new_opts = data["options"]
        stats_changes = data.get("stats_changes", {})
        new_items = data.get("new_items", [])
        used_items = data.get("used_items", []) 
        
        new_stats = self.stats.copy()
        for k, v in stats_changes.items():
            new_val = new_stats.get(k, 0) + v
            # 限制最小值为 0（除非你允许负值，否则推荐）
            if new_val < 0:
                new_val = 0
            new_stats[k] = new_val
        new_inventory = self.inventory.copy()

        # 去重：只添加名称不在当前物品栏中的新物品
        existing_names = {item.get("name") for item in self.inventory}
        for item in new_items:
            if item.get("name") not in existing_names:
                new_inventory.append(item)
            else:
                print(f"物品 {item.get('name')} 已存在，跳过添加")

        for used in used_items:
                item_name = used.get("name")
                if not item_name:
                    continue
                # 找到第一个匹配的物品并移除
                for i, item in enumerate(new_inventory):
                    if item.get("name") == item_name:
                        del new_inventory[i]
                        break
        
        
        
        self.stats = new_stats
        self.inventory = new_inventory
        self.history.append((new_desc, new_opts, self.stats.copy(), self.inventory.copy()))
        # 更新当前选项为新的选项，并为它们预生成分支（递归）
        self.current_options = new_opts
        self.branch_cache.clear()  # 清空旧缓存
        self._pregenerate_branches()  # 异步生成下一级分支
        return new_desc, new_opts, self.stats, self.inventory

def background_task(session_id: str, prompt: str):
    try:
        sessions[session_id]["progress"] = 5
        time.sleep(0.2)
        session = GameSession(prompt)
        sessions[session_id]["session"] = session
        sessions[session_id]["progress"] = 100
        sessions[session_id]["status"] = "done"
        desc, opts, stats, inv = session.history[0]
        sessions[session_id]["result"] = {
            "description": desc,
            "stats": stats,
            "inventory": inv,   # 注意：初始 inventory 为空
            "options": opts
        }
    except Exception as e:
        sessions[session_id]["status"] = "error"
        sessions[session_id]["error"] = str(e)
        sessions[session_id]["progress"] = 0

@app.post("/api/start_game")
async def start_game(req: dict):
    prompt = req.get("prompt")
    if not prompt:
        raise HTTPException(400, "prompt is required")
    session_id = str(uuid.uuid4())
    sessions[session_id] = {
        "status": "running",
        "progress": 0,
        "session": None,
        "result": None
    }
    thread = threading.Thread(target=background_task, args=(session_id, prompt))
    thread.daemon = True
    thread.start()
    tasks[session_id] = thread
    return {"task_id": session_id}

@app.get("/api/game_status")
async def game_status(task_id: str):
    if task_id not in sessions:
        raise HTTPException(404, "Task not found")
    data = sessions[task_id]
    return {
        "status": data["status"],
        "progress": data["progress"],
        "session_id": task_id if data["status"] == "done" else None,
        "error": data.get("error")
    }

@app.get("/api/game_state")
async def game_state(session_id: str):
    if session_id not in sessions:
        raise HTTPException(404, "Session not found")
    data = sessions[session_id]
    if data["status"] != "done":
        raise HTTPException(400, "Game not ready")
    result = data["result"]
    return {
        "description": result["description"],
        "stats": result.get("stats", result.get("resources", {})),
        "inventory": result.get("inventory", []),
        "options": result["options"]
    }

@app.post("/api/take_action")
async def take_action(req: dict):
    session_id = req.get("session_id")
    option = req.get("option")
    effect = req.get("effect", "")
    # 将 effect 传递给 generate_next_story
    
    if session_id not in sessions:
        raise HTTPException(404, "Session not found")
    session_obj = sessions[session_id]["session"]
    try:
        new_desc, new_opts, new_stats, new_inventory = session_obj.take_action(option, effect)
        return {
            "status": "continue",
            "description": new_desc,
            "stats": new_stats,
            "inventory": new_inventory,
            "options": new_opts
        }
    except Exception as e:
        # 打印详细错误到终端
        traceback.print_exc()
        raise HTTPException(500, detail=str(e))

@app.post("/api/cancel")
async def cancel(task_id: str):
    if task_id in tasks:
        # 简单标记取消，后台线程会检查
        sessions[task_id]["status"] = "cancelled"
    return {"ok": True}



# 根路径返回门户页面
@app.get("/")
async def root():
    return FileResponse(os.path.join(ROOT_DIR, "frontend", "index.html"))

@app.get("/loading.html")
async def loading():
    return FileResponse("frontend/loading.html")

@app.get("/game.html")
async def game():
    return FileResponse("frontend/game.html")

# 在文件末尾添加直接运行入口
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
