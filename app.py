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
for key in ("risks", "report", "elapsed", "total_terms", "failed_terms",
            "retry_count", "retry_status"):
    if key not in st.session_state:
        st.session_state[key] = None if key in ("risks", "report", "retry_status") else (
            [] if key == "failed_terms" else 0)

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
    delay = st.slider("请求间隔 (秒)", 1.0, 10.0, 2.0, 0.5,
                      help="默认 2 秒。并发查5办公室，403快速跳过不等待，失败词可一键重查")

# --- Main Area ---
st.title("WIPO 商标风险排查工具")
st.markdown("输入产品描述或营销文案，自动排查其中可能涉及 "
            "WIPO 注册商标的风险词汇。")

text = st.text_area("待排查文本", height=200,
                    placeholder="粘贴你的产品描述、营销文案等...\n提示：换行可以分隔不同短语，词组只在同一行内组合",
                    help="系统会自动提取单词和词组进行查询。词组只在同一行内生成，换行可以避免不相邻的词被组合在一起。例如分行输入 'Hello Kitty\\nOutdoor Toy' 只会查 'hello kitty' 和 'outdoor toy'，不会查 'kitty outdoor'。")

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
            est = len(terms) * (8 + delay)
            hint = ""
            if len(terms) > 25:
                hint = f"\n\n⚠️ 候选词较多（{len(terms)}个），建议分批查询（每批15个左右），否则实际耗时会更长且容易触发风控。"
            st.info(f"**候选词数**：{len(terms)}\n\n"
                    f"**预计耗时**：{est / 60:.1f} 分钟\n\n"
                    f"**候选词**：{', '.join(terms)}{hint}")

with col2:
    start_clicked = st.button("开始排查", type="primary",
                              use_container_width=True)

with col3:
    if st.button("清除结果", use_container_width=True):
        for k in ("risks", "report", "failed_terms"):
            st.session_state[k] = None if k in ("risks", "report") else []
        st.session_state["retry_count"] = 0
        st.session_state["retry_status"] = None
        st.rerun()

# --- Run Check ---
if start_clicked:
    if not text.strip():
        st.warning("请先输入文本")
    elif not offices:
        st.warning("请至少选择一个 IP 办公室")
    else:
        st.session_state["retry_count"] = 0
        st.session_state["retry_status"] = None
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
            result = check_text(
                text, offices=offices, nice_class=nice_class,
                status=status, delay=delay,
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
                text, risks, offices, nice_class, status)
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
            st.stop()

# --- Show Results ---
if st.session_state.get("risks") is not None:
    st.divider()
    risks = st.session_state["risks"]
    report = st.session_state["report"]
    failed_terms = st.session_state.get("failed_terms", [])

    m1, m2, m3 = st.columns(3)
    m1.metric("候选词数", st.session_state.get("total_terms") or "-")
    m2.metric("风险词数", len(risks))
    m3.metric("耗时", f"{st.session_state.get('elapsed', 0):.0f}秒")

    # 持久化显示重查结果（不随 rerun 消失）
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
                with st.spinner("重查失败词条..."):
                    retry_terms = [ft["term"] for ft in failed_terms]
                    retry_logs = []

                    def retry_log(msg):
                        retry_logs.append(msg)

                    retry_bar = st.progress(0, text="重查中...")

                    def retry_progress(current, total_count, term):
                        pct = current / total_count if total_count > 0 else 0
                        retry_bar.progress(pct,
                            text=f"[{current}/{total_count}] 重查: {term}")

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

                        # Replace risks for retried terms with fresh results
                        retry_term_set = {ft["term"] for ft in failed_terms}
                        risks = [r for r in risks
                                 if r["term"] not in retry_term_set]
                        risks.extend(retry_risks)

                        st.session_state["risks"] = risks
                        st.session_state["failed_terms"] = retry_failed
                        st.session_state["retry_count"] = retry_count + 1
                        st.session_state["report"] = generate_report(
                            text, risks, offices, nice_class, status)
                        st.session_state["retry_status"] = {
                            "retried_count": len(retry_terms),
                            "new_risks_count": len(retry_risks),
                            "still_failed_count": len(retry_failed),
                        }
                        retry_bar.progress(1.0, text="重查完成！")
                        st.rerun()
                    except Exception as e:
                        st.error(f"重查失败：{e}")
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
