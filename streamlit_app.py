import streamlit as st
import cadquery as cq
from cadquery import exporters
import tempfile
import os
import pyvista as pv
from stpyvista import stpyvista
import json
from stpyvista.utils import start_xvfb

if "IS_XVFB_RUNNING" not in st.session_state:
  start_xvfb()
  st.session_state.IS_XVFB_RUNNING = True 
  
# ------------- i18n加载 -------------
with open("i18n_strings.json", "r", encoding="utf-8") as f:
    I18N_STRINGS = json.load(f)


def _(key: str) -> str:
    """
    根据当前语言 lang 返回对应的翻译文案.
    如果对应语言里没有, 则回退到英文.
    """
    lang = st.session_state.get("selected_lang", "en")
    return I18N_STRINGS.get(lang, I18N_STRINGS["en"]).get(key, key)


# ------------- 几何参数 -------------
UNIT_LENGTH = 8.0
PLATE_HEIGHT = 3.2
ROOF_THICKNESS = 1.0
WALL_THICKNESS = 1.5
STUD_DIAMETER = 4.8
STUD_HEIGHT = 1.8
UNDERTUBE_OUTER_DIAM = 6.41
UNDERTUBE_INNER_DIAM = 4.8
PLAY = 0.2

def build_brick(
    brick_length=3,
    brick_width=2,
    brick_height=3,
    with_studs=True,
    tolerance=0.0
):
    outer_length = brick_length * UNIT_LENGTH
    outer_width  = brick_width  * UNIT_LENGTH
    outer_height = brick_height * PLATE_HEIGHT

    base = (
        cq.Workplane("XY")
        .box(outer_length, outer_width, outer_height)
        .translate((0, 0, outer_height/2))
    )

    cavity_length = outer_length - 2*WALL_THICKNESS - PLAY + 2*tolerance
    cavity_width  = outer_width  - 2*WALL_THICKNESS - PLAY + 2*tolerance
    cavity_height = outer_height - ROOF_THICKNESS

    inner_cavity = (
        cq.Workplane("XY")
        .translate((WALL_THICKNESS, WALL_THICKNESS, 0))
        .box(cavity_length, cavity_width, cavity_height)
    )
    shift_x = -outer_length / 2.0
    shift_y = -outer_width  / 2.0

    base = base.cut(inner_cavity).translate(((-shift_x, -shift_y, 0)))

    studs = None
    if with_studs:
        stud_cyl = cq.Workplane("XY")
        for x in range(brick_length):
            for y in range(brick_width):
                center_x = (x + 0.5) * UNIT_LENGTH
                center_y = (y + 0.5) * UNIT_LENGTH
                stud_cyl = (
                    stud_cyl
                    .pushPoints([(center_x, center_y)])
                    .circle((STUD_DIAMETER - 2*tolerance)/2.0)
                    .extrude(STUD_HEIGHT)
                )
        studs = stud_cyl.translate((0, 0, outer_height))

    under_tubes = None
    if brick_length > 1 and brick_width > 1:
        tube_height = outer_height - ROOF_THICKNESS + 0.01
        outer_rad = (UNDERTUBE_OUTER_DIAM - 2*tolerance) / 2.0
        inner_rad = (UNDERTUBE_INNER_DIAM + 2*tolerance) / 2.0

        ring_positions = [
            (x * UNIT_LENGTH, y * UNIT_LENGTH)
            for x in range(1, brick_length)
            for y in range(1, brick_width)
        ]

        tube_cyl = (
            cq.Workplane("XY")
            .pushPoints(ring_positions)
            .circle(outer_rad)
            .extrude(tube_height)
        )
        inner_cyl = (
            cq.Workplane("XY")
            .pushPoints(ring_positions)
            .circle(inner_rad)
            .extrude(tube_height)
            .translate((0, 0, -0.01))
        )
        under_tubes = tube_cyl.cut(inner_cyl)

    brick = base
    if studs:
        brick = brick.union(studs)
    if under_tubes:
        brick = brick.union(under_tubes)

    brick = brick.translate((shift_x, shift_y, 0))
    return brick


def main():
    st.title( _("app_title") )

    # 确保 session_state 有初始数据
    if "selected_lang" not in st.session_state:
        st.session_state["selected_lang"] = "en"
    if "brick_params" not in st.session_state:
        # 存储上一次生成的参数(长度/宽度/高度/公差/是否带studs等)
        st.session_state["brick_params"] = {
            "brick_length": 3,
            "brick_width": 2,
            "brick_height": 3,
            "with_studs": True,
            "tolerance": -0.1
        }
    if "brick_model" not in st.session_state:
        # 存储生成后的模型(或可只存参数, 动态生成)
        st.session_state["brick_model"] = None
    if "generate_count" not in st.session_state:
        st.session_state["generate_count"] = 0

    # ------------- 侧边栏 -------------
    # 1) 语言切换(不在表单中, 这样切换语言时不需要点Generate)
    selected_lang = st.sidebar.selectbox(
        label=_("sidebar_language"),
        options=["en", "zh"],
        format_func=lambda x: "English" if x == "en" else "中文",
        key="selected_lang",  # 直接和 session_state["selected_lang"] 绑定
    )

    # 2) 参数表单
    with st.sidebar.form("param_form"):
        # 默认从 session_state["brick_params"] 里取当前值
        current_params = st.session_state["brick_params"]

        length_val = st.slider(
            label=_("sidebar_length"),
            min_value=1, max_value=48,
            value=current_params["brick_length"],
        )
        width_val = st.slider(
            label=_("sidebar_width"),
            min_value=1, max_value=48,
            value=current_params["brick_width"],
        )
        height_val = st.slider(
            label=_("sidebar_height"),
            min_value=1, max_value=10,
            value=current_params["brick_height"],
        )

        studs_opt = st.selectbox(
            label=_("sidebar_studs"),
            options=[ _("studs_yes"), _("studs_no") ],
            index=(0 if current_params["with_studs"] else 1)
        )
        # 映射回 True/False
        with_studs_val = (studs_opt == _("studs_yes"))

        tol_val = st.number_input(
            label=_("sidebar_tolerance"),
            value=current_params["tolerance"],
            step=0.01
        )

        generate_button = st.form_submit_button( label=_("btn_generate") )

    # ------------- 点击 Generate 时 -------------
    if generate_button:
        # 更新 session_state["brick_params"]
        st.session_state["brick_params"] = {
            "brick_length": length_val,
            "brick_width": width_val,
            "brick_height": height_val,
            "with_studs": with_studs_val,
            "tolerance": tol_val
        }

        # 生成新模型
        new_model = build_brick(
            brick_length=length_val,
            brick_width=width_val,
            brick_height=height_val,
            with_studs=with_studs_val,
            tolerance=tol_val
        )
        st.session_state["brick_model"] = new_model
        st.session_state["generate_count"] += 1  # 强制 stpyvista 重绘

    # ------------- 在主区域显示 3D -------------
    if st.session_state["brick_model"] is None:
        st.info( _("no_model") )
    else:
        # 导出 STL -> PyVista
        with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as tmp_stl:
            tmp_stl_path = tmp_stl.name
        exporters.export(st.session_state["brick_model"], tmp_stl_path, exporters.ExportTypes.STL)
        mesh = pv.read(tmp_stl_path)
        os.remove(tmp_stl_path)

        # 绘制可交互 3D
        plotter = pv.Plotter(window_size=(600, 500))
        plotter.add_mesh(mesh, color="orange", show_edges=False)
        plotter.view_isometric()

        stpyvista(plotter, key=f"interactive_brick_{st.session_state['generate_count']}")

        # 下载 STL
        with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as tmp_stl:
            tmp_stl_path = tmp_stl.name
        exporters.export(st.session_state["brick_model"], tmp_stl_path, exporters.ExportTypes.STL)
        with open(tmp_stl_path, "rb") as f:
            stl_data = f.read()
        os.remove(tmp_stl_path)

        st.download_button(
            label=_("download_stl"),
            data=stl_data,
            file_name="brick_brick.stl",
            mime="application/vnd.ms-pki.stl"
        )

        # 下载 STEP
        with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as tmp_step:
            tmp_step_path = tmp_step.name
        exporters.export(st.session_state["brick_model"], tmp_step_path, exporters.ExportTypes.STEP)
        with open(tmp_step_path, "rb") as f:
            step_data = f.read()
        os.remove(tmp_step_path)

        st.download_button(
            label=_("download_step"),
            data=step_data,
            file_name="brick_brick.step",
            mime="application/x-step"
        )


if __name__ == "__main__":
    main()
