#!/usr/bin/env python3
"""WIPO 商标风险排查 — Streamlit Web 应用"""

import time
import streamlit as st
from wipo_checker import (
    extract_terms, check_text, generate_report,
    ensure_crypto_js, DEFAULT_OFFICES, DEFAULT_NICE_CLASS, DEFAULT_STATUS,
)

st.set_page_config(page_title="WIPO 商标风险排查", page_icon="🔍",
                   layout="wide")

# --- Session State ---
for key in ("risks", "report", "elapsed", "total_terms"):
    if key not in st.session_state:
        st.session_state[key] = None if key in ("risks", "report") else 0

# --- Sidebar ---
with st.sidebar:
    st.header("设置")
    all_offices = ["US", "CA", "DE", "FR", "GB", "ES", "IT", "NL",
                   "BE", "AU", "JP"]
    offices = st.multiselect("IP 办公室", all_offices, default=DEFAULT_OFFICES)
    nice_class = st.text_input("Nice 分类", value=DEFAULT_NICE_CLASS,
                                help="28 = 玩具/游戏/体育用品")
    status = st.selectbox("商标状态",
                          ["Registered", "Pending", "Ended", "Expired"],
                          index=0)
    delay = st.slider("请求间隔 (秒)", 1.0, 10.0, 3.0, 0.5,
                      help="建议 3 秒以上，遇到 403 限流会自动增加")

# --- Main Area ---
st.title("WIPO 商标风险排查工具")
st.markdown("输入产品描述或营销文案，自动排查其中可能涉及 "
            "WIPO 注册商标的风险词汇。")

text = st.text_area("待排查文本", height=200,
                    placeholder="粘贴你的产品描述、营销文案等...")

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
            est = len(terms) * (4 + delay)
            st.info(f"**候选词数**：{len(terms)}\n\n"
                    f"**预计耗时**：{est / 60:.1f} 分钟\n\n"
                    f"**候选词**：{', '.join(terms)}")

with col2:
    start_clicked = st.button("开始排查", type="primary",
                              use_container_width=True)

with col3:
    if st.button("清除结果", use_container_width=True):
        for k in ("risks", "report"):
            st.session_state[k] = None
        st.rerun()

# --- Run Check ---
if start_clicked:
    if not text.strip():
        st.warning("请先输入文本")
    elif not offices:
        st.warning("请至少选择一个 IP 办公室")
    else:
        with st.spinner("检查依赖..."):
            try:
                ensure_crypto_js(log=lambda m: None)
            except Exception as e:
                st.error(f"依赖安装失败：{e}")
                st.stop()

        terms = extract_terms(text)
        total = len(terms)

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

        start_time = time.time()
        try:
            risks = check_text(
                text, offices=offices, nice_class=nice_class,
                status=status, delay=delay,
                progress_callback=progress_callback,
                log_callback=log_callback,
            )
            elapsed = time.time() - start_time
            progress_bar.progress(1.0, text="排查完成！")

            st.session_state["risks"] = risks
            st.session_state["report"] = generate_report(
                text, risks, offices, nice_class, status)
            st.session_state["elapsed"] = elapsed
            st.session_state["total_terms"] = total
            st.success(f"排查完成！耗时 {elapsed:.1f} 秒，"
                       f"发现 {len(risks)} 个风险词。")
        except Exception as e:
            st.error(f"排查失败：{e}")
            st.stop()

# --- Show Results ---
if st.session_state.get("risks") is not None:
    st.divider()
    risks = st.session_state["risks"]
    report = st.session_state["report"]

    m1, m2, m3 = st.columns(3)
    m1.metric("候选词数", st.session_state.get("total_terms") or "-")
    m2.metric("风险词数", len(risks))
    m3.metric("耗时", f"{st.session_state.get('elapsed', 0):.0f}秒")

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
