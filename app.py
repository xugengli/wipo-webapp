#!/usr/bin/env python3
"""WIPO 商标风险排查 — Streamlit Web 应用"""

import time
import json
import base64
import hmac
import hashlib
import threading
import streamlit as st
from wipo_checker import (
    extract_terms, check_text, generate_report,
    ensure_crypto_js, DEFAULT_OFFICES, DEFAULT_NICE_CLASS, DEFAULT_STATUS,
)

st.set_page_config(page_title="WIPO 商标风险排查", page_icon="🔍",
                   layout="wide")

# ============================================================
#  Auth: HMAC 签名 token + st.query_params 持久化 (7天)
# ============================================================

AUTH_SECRET = st.secrets.get("auth_secret", "wipo-change-this-secret")


def _create_token(username: str, expiry_days: int = 7) -> str:
    """生成 HMAC 签名 token。"""
    payload = {"u": username, "exp": int(time.time()) + expiry_days * 86400}
    raw = json.dumps(payload, separators=(",", ":"))
    b64 = base64.urlsafe_b64encode(raw.encode()).decode()
    sig = hmac.new(AUTH_SECRET.encode(), b64.encode(), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def _verify_token(token: str) -> str | None:
    """验证 token，返回 username 或 None。"""
    try:
        b64, sig = token.split(".", 1)
        expected = hmac.new(AUTH_SECRET.encode(), b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(b64))
        if time.time() > payload["exp"]:
            return None
        return payload["u"]
    except Exception:
        return None


def _check_auth() -> str | None:
    """检查当前是否已登录，返回 username 或 None。"""
    # 1) session_state 已有
    if st.session_state.get("_auth_user"):
        return st.session_state["_auth_user"]
    # 2) query_params 有 token
    token = st.query_params.get("auth_token")
    if token:
        user = _verify_token(token)
        if user:
            st.session_state["_auth_user"] = user
            return user
    return None


def _do_login():
    """渲染登录页面。"""
    st.title("WIPO 商标风险排查工具")
    st.markdown("请登录后使用。")
    st.divider()

    with st.form("login_form"):
        username = st.text_input("用户名")
        password = st.text_input("密码", type="password")
        submitted = st.form_submit_button("登录", type="primary",
                                          use_container_width=True)
        if submitted:
            users = st.secrets.get("users", {})
            if username in users and users[username] == password:
                token = _create_token(username)
                st.query_params["auth_token"] = token
                st.session_state["_auth_user"] = username
                st.rerun()
            else:
                st.error("用户名或密码错误")

    st.stop()


# --- 登录门禁 ---
_current_user = _check_auth()
if not _current_user:
    _do_login()
username = _current_user

# ============================================================
#  Global State: 跨 session 共享的锁 + 排队状态
# ============================================================

LOCK_TIMEOUT = 600  # 10 分钟自动超时释放
QUEUE_TIMEOUT = 90  # 排队中 90 秒无轮询自动移除


@st.cache_resource
def get_global_state():
    """所有 session 共享的全局状态（Streamlit Cloud 单进程）。"""
    return {
        "mutex": threading.Lock(),
        "lock_holder": None,           # 正在排查的用户名
        "lock_acquired_at": 0.0,       # 获取锁的时间戳
        "current_progress": {          # 当前排查进度
            "current": 0,
            "total": 0,
            "term": "",
        },
        "queue": [],                   # [{username, joined_at, term_count}]
    }


def try_acquire_lock(user: str, term_count: int) -> bool:
    """尝试获取全局锁。FIFO：只有队列第 1 名才能获取。"""
    gs = get_global_state()
    with gs["mutex"]:
        now = time.time()
        # 清理超时的排队者
        gs["queue"] = [
            q for q in gs["queue"]
            if now - q["joined_at"] < QUEUE_TIMEOUT or q["username"] == user
        ]
        # 清理超时的锁
        if gs["lock_holder"] and now - gs["lock_acquired_at"] > LOCK_TIMEOUT:
            gs["lock_holder"] = None
        # 锁被占用
        if gs["lock_holder"] is not None:
            return False
        # 队列非空时，只有第 1 名能拿锁
        if gs["queue"] and gs["queue"][0]["username"] != user:
            return False
        # 获取锁
        gs["lock_holder"] = user
        gs["lock_acquired_at"] = now
        gs["queue"] = [q for q in gs["queue"] if q["username"] != user]
        gs["current_progress"] = {"current": 0, "total": term_count, "term": ""}
        return True


def release_lock(user: str):
    """释放全局锁。"""
    gs = get_global_state()
    with gs["mutex"]:
        if gs["lock_holder"] == user:
            gs["lock_holder"] = None
            gs["lock_acquired_at"] = 0.0
            gs["current_progress"] = {"current": 0, "total": 0, "term": ""}


def add_to_queue(user: str, term_count: int):
    """加入排队队列（如果已在队列则更新时间戳和词数）。"""
    gs = get_global_state()
    with gs["mutex"]:
        gs["queue"] = [q for q in gs["queue"] if q["username"] != user]
        gs["queue"].append({
            "username": user,
            "joined_at": time.time(),
            "term_count": term_count,
        })


def remove_from_queue(user: str):
    """从排队队列移除。"""
    gs = get_global_state()
    with gs["mutex"]:
        gs["queue"] = [q for q in gs["queue"] if q["username"] != user]


def get_queue_info(user: str) -> dict:
    """获取当前排队信息。"""
    gs = get_global_state()
    with gs["mutex"]:
        now = time.time()
        # 清理超时排队者
        gs["queue"] = [
            q for q in gs["queue"]
            if now - q["joined_at"] < QUEUE_TIMEOUT or q["username"] == user
        ]
        holder = gs["lock_holder"]
        progress = dict(gs["current_progress"])
        queue = list(gs["queue"])
        my_pos = -1
        for i, q in enumerate(queue):
            if q["username"] == user:
                my_pos = i
                break
        return {
            "holder": holder,
            "progress": progress,
            "queue": queue,
            "my_position": my_pos,
        }


# ============================================================
#  Session State
# ============================================================

for key in ("risks", "report", "elapsed", "total_terms", "failed_terms",
            "retry_count", "retry_status", "all_terms", "selected_terms",
            "pending_check", "pending_params"):
    if key not in st.session_state:
        if key in ("risks", "report", "retry_status", "all_terms",
                   "selected_terms", "pending_check", "pending_params"):
            st.session_state[key] = None
        elif key == "failed_terms":
            st.session_state[key] = []
        else:
            st.session_state[key] = 0

# ============================================================
#  Sidebar
# ============================================================

with st.sidebar:
    st.markdown(f"👤 **{username}**")
    if st.button("退出登录", use_container_width=True):
        st.query_params.clear()
        st.session_state.pop("_auth_user", None)
        st.rerun()

    st.divider()
    st.header("设置")
    all_offices = ["US", "CA", "DE", "FR", "GB", "ES", "IT", "NL",
                   "BE", "AU", "JP"]
    offices = st.multiselect("IP 办公室", all_offices, default=DEFAULT_OFFICES)
    nice_class = st.text_input("Nice 分类", value=DEFAULT_NICE_CLASS,
                                help="28 = 玩具/游戏/体育用品")
    status = st.selectbox("商标状态",
                          ["Registered", "Pending", "Ended", "Expired"],
                          index=0)
    delay = st.slider("请求间隔 (秒)", 1.0, 10.0, 2.0, 0.5,
                      help="默认 2 秒。并发查5办公室，403快速跳过不等待，"
                           "失败词可一键重查。多人同时使用时系统会自动排队。")

# ============================================================
#  Main Area
# ============================================================

st.title("WIPO 商标风险排查工具")
st.markdown("输入产品描述或营销文案，自动排查其中可能涉及 "
            "WIPO 注册商标的风险词汇。")

text = st.text_area("待排查文本", height=200,
                    placeholder="粘贴你的产品描述、营销文案等...\n"
                                "提示：换行可以分隔不同短语，词组只在同一行内组合",
                    help="系统会自动提取单词和词组进行查询。词组只在同一行内生成，"
                         "换行可以避免不相邻的词被组合在一起。")

uploaded = st.file_uploader("或上传文本文件 (.txt / .md)",
                            type=["txt", "md"])
if uploaded is not None:
    text = uploaded.read().decode("utf-8")
    st.info(f"已加载文件：{uploaded.name}（{len(text)} 字符）")

col1, col2, col3 = st.columns(3)

with col1:
    if st.button("预估候选词", type="secondary", use_container_width=True):
        if not text.strip():
            st.warning("请先输入文本")
        else:
            terms = extract_terms(text)
            st.session_state["all_terms"] = terms
            st.session_state["selected_terms"] = list(terms)
            st.session_state.pop("term_selector", None)
            st.rerun()

with col2:
    start_clicked = st.button("开始排查", type="primary",
                              use_container_width=True)

with col3:
    if st.button("清除结果", use_container_width=True):
        for k in ("risks", "report", "failed_terms", "all_terms",
                  "selected_terms", "pending_check", "pending_params",
                  "retry_status"):
            st.session_state[k] = (None if k in ("risks", "report",
                                "retry_status", "all_terms",
                                "selected_terms", "pending_check",
                                "pending_params") else [])
        st.session_state["retry_count"] = 0
        remove_from_queue(username)
        st.rerun()

# --- 候选词编辑窗口 ---
if st.session_state.get("all_terms"):
    all_terms = st.session_state["all_terms"]
    selected = st.multiselect(
        "候选词（点击词右侧 × 移除不需要的词）",
        options=all_terms,
        default=st.session_state.get("selected_terms", all_terms),
        key="term_selector",
    )
    st.session_state["selected_terms"] = selected

    est = len(selected) * (8 + delay)
    hint = ""
    if len(selected) > 25:
        hint = (f"\n\n⚠️ 候选词较多（{len(selected)}个），建议分批查询"
                f"（每批15个左右），否则实际耗时会更长且容易触发风控。")
    st.info(f"**已选候选词**：{len(selected)} / {len(all_terms)}\n\n"
            f"**预计耗时**：{est / 60:.1f} 分钟{hint}")

# ============================================================
#  排队中：轮询获取锁
# ============================================================

if st.session_state.get("pending_check"):
    params = st.session_state.get("pending_params", {})
    terms_to_check = params.get("terms", [])
    term_count = len(terms_to_check)

    if try_acquire_lock(username, term_count):
        # 终于拿到锁了！（pending_params 保留，下方执行排查时会读取并清除）
        st.session_state["pending_check"] = False
        _run_check = True
    else:
        info = get_queue_info(username)
        holder = info["holder"]
        prog = info["progress"]
        my_pos = info["my_position"]

        # 更新自己的排队时间戳（证明还活着）
        add_to_queue(username, term_count)

        # 显示排队状态
        cur_idx = prog.get("current", 0)
        cur_total = prog.get("total", 0)
        cur_term = prog.get("term", "")
        holder_remaining = max(0, cur_total - cur_idx)
        holder_eta = holder_remaining * (8 + delay)

        # 计算总等待时间
        wait_sec = holder_eta
        queue_list = info["queue"]
        for i in range(my_pos):
            if i < len(queue_list):
                wait_sec += queue_list[i]["term_count"] * (8 + delay)

        st.warning(
            f"⏳ **系统繁忙，正在排队...**\n\n"
            f"**{holder}** 正在排查 "
            f"[{cur_idx}/{cur_total}] `{cur_term}`\n\n"
            f"你在队列中排第 **{my_pos + 1}** 位\n\n"
            f"预计等待约 **{wait_sec / 60:.1f}** 分钟"
        )
        st.progress(cur_idx / cur_total if cur_total > 0 else 0,
                    text=f"{holder}: [{cur_idx}/{cur_total}] {cur_term}")

        time.sleep(3)
        st.rerun()
else:
    _run_check = False

# ============================================================
#  开始排查
# ============================================================

if start_clicked:
    if not text.strip():
        st.warning("请先输入文本")
        st.stop()
    elif not offices:
        st.warning("请至少选择一个 IP 办公室")
        st.stop()
    else:
        # 确定要查的词
        if st.session_state.get("selected_terms"):
            terms_to_check = st.session_state["selected_terms"]
        else:
            terms_to_check = extract_terms(text)

        if not terms_to_check:
            st.warning("没有候选词可查")
            st.stop()

        term_count = len(terms_to_check)

        # 尝试获取全局锁
        if try_acquire_lock(username, term_count):
            _run_check = True
        else:
            # 排队
            add_to_queue(username, term_count)
            st.session_state["pending_check"] = True
            st.session_state["pending_params"] = {
                "terms": terms_to_check,
                "offices": offices,
                "nice_class": nice_class,
                "status": status,
                "delay": delay,
            }
            st.rerun()

# ============================================================
#  执行排查（锁已获取）
# ============================================================

if _run_check:
    # 从 pending_params 或当前点击获取参数
    if st.session_state.get("pending_params"):
        params = st.session_state["pending_params"]
        terms_to_check = params["terms"]
        run_offices = params["offices"]
        run_nice_class = params["nice_class"]
        run_status = params["status"]
        run_delay = params["delay"]
    else:
        if st.session_state.get("selected_terms"):
            terms_to_check = st.session_state["selected_terms"]
        else:
            terms_to_check = extract_terms(text)
        run_offices = offices
        run_nice_class = nice_class
        run_status = status
        run_delay = delay

    st.session_state["retry_count"] = 0
    st.session_state["retry_status"] = None

    with st.spinner("检查依赖..."):
        try:
            ensure_crypto_js(log=lambda m: None)
        except Exception as e:
            st.error(f"依赖安装失败：{e}")
            release_lock(username)
            st.stop()

    total = len(terms_to_check)
    progress_bar = st.progress(0, text="准备中...")
    log_placeholder = st.empty()
    logs = []

    def log_callback(msg):
        logs.append(msg)
        log_placeholder.text("\n".join(logs[-8:]))

    def progress_callback(current, total_count, term):
        pct = current / total_count if total_count > 0 else 0
        progress_bar.progress(pct,
            text=f"[{current}/{total_count}] 正在检查: {term}")
        # 更新全局状态（让排队的人看到进度）
        gs = get_global_state()
        gs["current_progress"] = {
            "current": current,
            "total": total_count,
            "term": term,
        }

    start_time = time.time()
    try:
        result = check_text(
            terms=terms_to_check, offices=run_offices,
            nice_class=run_nice_class, status=run_status,
            delay=run_delay,
            progress_callback=progress_callback,
            log_callback=log_callback,
        )
        risks = result["risks"]
        failed_terms = result.get("failed_terms", [])
        elapsed = time.time() - start_time
        progress_bar.progress(1.0, text="排查完成！")

        st.session_state["risks"] = risks
        st.session_state["failed_terms"] = failed_terms
        st.session_state["report"] = generate_report(
            text, risks, run_offices, run_nice_class, run_status,
            total_terms=total)
        st.session_state["elapsed"] = elapsed
        st.session_state["total_terms"] = total
        if failed_terms:
            st.error(f"排查完成但存在遗漏！耗时 {elapsed:.1f} 秒，"
                     f"发现 {len(risks)} 个风险词。"
                     f"{len(failed_terms)} 个词条的办公室未查全，"
                     f"详见下方警告。")
        else:
            st.success(f"排查完成！耗时 {elapsed:.1f} 秒，"
                       f"发现 {len(risks)} 个风险词。")
    except Exception as e:
        st.error(f"排查失败：{e}")
    finally:
        release_lock(username)
        st.session_state["pending_params"] = None

# ============================================================
#  显示结果
# ============================================================

if st.session_state.get("risks") is not None:
    st.divider()
    risks = st.session_state["risks"]
    report = st.session_state["report"]
    failed_terms = st.session_state.get("failed_terms", [])

    m1, m2, m3 = st.columns(3)
    m1.metric("候选词数", st.session_state.get("total_terms") or "-")
    m2.metric("风险词数", len(risks))
    m3.metric("耗时", f"{st.session_state.get('elapsed', 0):.0f}秒")

    # 持久化显示重查结果
    retry_status = st.session_state.get("retry_status")
    if retry_status:
        retried = retry_status["retried_count"]
        new_risks = retry_status["new_risks_count"]
        still_failed = retry_status["still_failed_count"]
        if still_failed == 0:
            if new_risks > 0:
                st.success(f"✅ 重查完成！{retried} 个词条已全部查全，"
                           f"新增 {new_risks} 个风险词，已整合到下方报告中。")
            else:
                st.success(f"✅ 重查完成！{retried} 个词条已全部查全，"
                           f"均未发现风险商标。")
        else:
            if new_risks > 0:
                st.warning(f"⚠️ 重查完成，仍有 {still_failed} 个词条未查全。"
                           f"已查到的 {new_risks} 个风险词已整合到下方报告中。")
            else:
                st.warning(f"⚠️ 重查完成，仍有 {still_failed} 个词条未查全。"
                           f"已查到的词条均未发现风险商标。")

    if failed_terms:
        retry_count = st.session_state.get("retry_count", 0)
        st.error(f"⚠️ 以下 {len(failed_terms)} 个词条的办公室未查全，"
                 f"结果可能不完整：")
        ft_data = []
        for ft in failed_terms:
            ft_data.append({
                "词条": ft["term"],
                "未查到的办公室": ", ".join(ft["offices"]),
            })
        st.dataframe(ft_data, use_container_width=True, hide_index=True)

        if retry_count < 1:
            if st.button("一键重查失败词", type="primary"):
                # 重查也需要获取全局锁
                retry_terms = [ft["term"] for ft in failed_terms]
                if try_acquire_lock(username, len(retry_terms)):
                    with st.spinner("重查失败词条..."):
                        retry_logs = []

                        def retry_log(msg):
                            retry_logs.append(msg)

                        retry_bar = st.progress(0, text="重查中...")

                        def retry_progress(current, total_count, term):
                            pct = current / total_count if total_count > 0 else 0
                            retry_bar.progress(pct,
                                text=f"[{current}/{total_count}] 重查: {term}")
                            gs = get_global_state()
                            gs["current_progress"] = {
                                "current": current,
                                "total": total_count,
                                "term": term,
                            }

                        try:
                            retry_result = check_text(
                                terms=retry_terms, offices=offices,
                                nice_class=nice_class, status=status,
                                delay=delay + 1.0,
                                progress_callback=retry_progress,
                                log_callback=retry_log,
                            )
                            retry_risks = retry_result["risks"]
                            retry_failed = retry_result.get("failed_terms", [])

                            # 用重查结果替换失败词的风险数据
                            retry_term_set = {ft["term"] for ft in failed_terms}
                            risks = [r for r in risks
                                     if r["term"] not in retry_term_set]
                            risks.extend(retry_risks)

                            st.session_state["risks"] = risks
                            st.session_state["failed_terms"] = retry_failed
                            st.session_state["retry_count"] = retry_count + 1
                            st.session_state["report"] = generate_report(
                                text, risks, offices, nice_class, status,
                                total_terms=st.session_state.get("total_terms"))
                            st.session_state["retry_status"] = {
                                "retried_count": len(retry_terms),
                                "new_risks_count": len(retry_risks),
                                "still_failed_count": len(retry_failed),
                            }
                            retry_bar.progress(1.0, text="重查完成！")
                            st.rerun()
                        except Exception as e:
                            st.error(f"重查失败：{e}")
                        finally:
                            release_lock(username)
                else:
                    info = get_queue_info(username)
                    holder = info["holder"]
                    st.warning(f"系统繁忙，**{holder}** 正在排查中。"
                               f"请稍后再次点击「一键重查失败词」。")
        else:
            st.warning("重查后仍有失败词条。云端IP可能被临时限制，"
                       "建议用飞书助手本地查询（本地IP几乎不触发403）。")
            st.code("\n".join([ft["term"] for ft in failed_terms]),
                    language="text")
            st.info("复制以上词条，粘贴到飞书「商标风险排查助手」中查询")

    if risks:
        st.subheader("风险词汇详情")
        table_data = []
        for item in risks:
            for ex in item["examples"]:
                table_data.append({
                    "风险词": item["term"],
                    "商标名称": ex["brandName"],
                    "办公室": ex["office"],
                    "注册号": ex.get("regNumber") or "—",
                    "状态": ex.get("status", "—"),
                })
        st.dataframe(table_data, use_container_width=True, hide_index=True)
    else:
        st.success("未发现明显风险词汇。")

    st.download_button(
        "下载报告 (Markdown)", report,
        file_name=f"wipo_report_{time.strftime('%Y%m%d_%H%M%S')}.md",
        mime="text/markdown",
    )
    with st.expander("查看完整报告"):
        st.markdown(report)
