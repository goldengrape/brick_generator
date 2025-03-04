import streamlit as st
import tempfile, os, json
import pyvista as pv
from stpyvista import stpyvista
from stpyvista.utils import start_xvfb
import sys

# 载入 i18n 字符串
@st.cache_data
def load_i18n():
    with open("i18n_strings.json", "r", encoding="utf-8") as f:
        return json.load(f)

I18N_STRINGS = load_i18n()

def _(key: str) -> str:
    """
    根据当前语言返回对应的翻译文案，
    如果对应语言中没有该翻译，则回退到英文。
    """
    lang = st.session_state.get("selected_lang", "en")
    return I18N_STRINGS.get(lang, I18N_STRINGS["en"]).get(key, key)

# pythonocc 模块
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCC.Core.gp import gp_Pnt, gp_Dir, gp_Ax2, gp_Trsf, gp_Vec
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Extend.DataExchange import write_stl_file, write_step_file

# 全局几何参数
UNIT_LENGTH = 8.0
PLATE_HEIGHT = 3.2
ROOF_THICKNESS = 1.0
WALL_THICKNESS = 1.5
STUD_DIAMETER = 4.8
STUD_HEIGHT = 1.8
UNDERTUBE_OUTER_DIAM = 6.41
UNDERTUBE_INNER_DIAM = 4.8
PLAY = 0.2

def build_brick(brick_length=3, brick_width=2, brick_height=3, with_studs=True, tolerance=0.0):
    """
    用 pythonocc 构造砖块模型：
      1. 生成外壳盒体；
      2. 从中减去内腔（内盒）；
      3. 根据参数添加顶部的圆柱 stud 以及底部的 under-tube（内部管）；
      4. 最后将整体平移使模型中心位于原点。
    """
    outer_length = brick_length * UNIT_LENGTH
    outer_width  = brick_width  * UNIT_LENGTH
    outer_height = brick_height * PLATE_HEIGHT

    # 生成外壳盒体（底部在 z=0）
    base_box = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), gp_Pnt(outer_length, outer_width, outer_height)).Shape()

    # 内腔尺寸（根据墙厚、顶板厚度及 PLAY 调整）
    cavity_length = outer_length - 2*WALL_THICKNESS - PLAY + 2*tolerance
    cavity_width  = outer_width  - 2*WALL_THICKNESS - PLAY + 2*tolerance
    cavity_height = outer_height - ROOF_THICKNESS
    cavity_box = BRepPrimAPI_MakeBox(gp_Pnt(WALL_THICKNESS, WALL_THICKNESS, 0),
                                     gp_Pnt(WALL_THICKNESS + cavity_length, WALL_THICKNESS + cavity_width, cavity_height)).Shape()

    # 从外壳中减去内腔
    brick_shape = BRepAlgoAPI_Cut(base_box, cavity_box).Shape()

    # 添加顶部 studs
    if with_studs:
        for i in range(brick_length):
            for j in range(brick_width):
                center_x = (i + 0.5) * UNIT_LENGTH
                center_y = (j + 0.5) * UNIT_LENGTH
                stud_radius = (STUD_DIAMETER - 2*tolerance) / 2.0
                stud = BRepPrimAPI_MakeCylinder(
                    gp_Ax2(gp_Pnt(center_x, center_y, outer_height), gp_Dir(0, 0, 1)),
                    stud_radius,
                    STUD_HEIGHT
                ).Shape()
                brick_shape = BRepAlgoAPI_Fuse(brick_shape, stud).Shape()

    # 添加底部 under tubes（仅当砖块大于 1×1 时）
    if brick_length > 1 and brick_width > 1:
        tube_height = outer_height - ROOF_THICKNESS + 0.01
        outer_rad = (UNDERTUBE_OUTER_DIAM - 2*tolerance) / 2.0
        inner_rad = (UNDERTUBE_INNER_DIAM + 2*tolerance) / 2.0
        for i in range(1, brick_length):
            for j in range(1, brick_width):
                pos_x = i * UNIT_LENGTH
                pos_y = j * UNIT_LENGTH
                tube_outer = BRepPrimAPI_MakeCylinder(
                    gp_Ax2(gp_Pnt(pos_x, pos_y, 0), gp_Dir(0, 0, 1)),
                    outer_rad,
                    tube_height
                ).Shape()
                tube_inner = BRepPrimAPI_MakeCylinder(
                    gp_Ax2(gp_Pnt(pos_x, pos_y, 0), gp_Dir(0, 0, 1)),
                    inner_rad,
                    tube_height
                ).Shape()
                tube = BRepAlgoAPI_Cut(tube_outer, tube_inner).Shape()
                brick_shape = BRepAlgoAPI_Fuse(brick_shape, tube).Shape()

    # 平移模型使中心位于原点
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(-outer_length/2.0, -outer_width/2.0, 0))
    transformer = BRepBuilderAPI_Transform(brick_shape, trsf, True)
    brick_shape = transformer.Shape()

    return brick_shape

def main():
    # 选择语言（放在侧边栏顶部）
    selected_lang = st.sidebar.selectbox("Language", options=["en", "zh"], index=1, key="selected_lang")
    
    st.title(_( "app_title" ))
    # 初始化 session_state 参数
    if "brick_params" not in st.session_state:
        st.session_state["brick_params"] = {
            "brick_length": 3,
            "brick_width": 2,
            "brick_height": 3,
            "with_studs": True,
            "tolerance": -0.1
        }
    if "brick_model" not in st.session_state:
        st.session_state["brick_model"] = None
    if "generate_count" not in st.session_state:
        st.session_state["generate_count"] = 0

    # 侧边栏参数表单
    with st.sidebar.form("param_form"):
        current_params = st.session_state["brick_params"]
        length_val = st.slider(_( "sidebar_length" ), min_value=1, max_value=48, value=current_params["brick_length"])
        width_val = st.slider(_( "sidebar_width" ), min_value=1, max_value=48, value=current_params["brick_width"])
        height_val = st.slider(_( "sidebar_height" ), min_value=1, max_value=10, value=current_params["brick_height"])
        studs_opt = st.selectbox(_( "sidebar_studs" ), options=[_( "studs_yes" ), _( "studs_no" )],
                                 index=(0 if current_params["with_studs"] else 1))
        with_studs_val = (studs_opt == _( "studs_yes" ))
        tol_val = st.number_input(_( "sidebar_tolerance" ), value=current_params["tolerance"], step=0.01)
        generate_button = st.form_submit_button(_( "btn_generate" ))

    if generate_button:
        st.session_state["brick_params"] = {
            "brick_length": length_val,
            "brick_width": width_val,
            "brick_height": height_val,
            "with_studs": with_studs_val,
            "tolerance": tol_val
        }
        model = build_brick(brick_length=length_val,
                            brick_width=width_val,
                            brick_height=height_val,
                            with_studs=with_studs_val,
                            tolerance=tol_val)
        st.session_state["brick_model"] = model
        st.session_state["generate_count"] += 1

    if st.session_state["brick_model"] is None:
        st.info(_( "no_model" ))
    else:
        # 导出 STL 并用 pyvista 显示
        with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as tmp_stl:
            tmp_stl_path = tmp_stl.name
        write_stl_file(st.session_state["brick_model"], tmp_stl_path)
        mesh = pv.read(tmp_stl_path)
        os.remove(tmp_stl_path)

        plotter = pv.Plotter(window_size=(600, 500))
        plotter.add_mesh(mesh, color="orange", show_edges=False)
        plotter.view_isometric()
        stpyvista(plotter, key=f"interactive_brick_{st.session_state['generate_count']}")

        # STL 下载按钮
        with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as tmp_stl2:
            tmp_stl_path2 = tmp_stl2.name
        write_stl_file(st.session_state["brick_model"], tmp_stl_path2)
        with open(tmp_stl_path2, "rb") as f:
            stl_data = f.read()
        os.remove(tmp_stl_path2)
        st.download_button(_( "download_stl" ), data=stl_data, file_name="brick_brick.stl", mime="application/vnd.ms-pki.stl")

        # STEP 下载按钮
        with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as tmp_step:
            tmp_step_path = tmp_step.name
        write_step_file(st.session_state["brick_model"], tmp_step_path)
        with open(tmp_step_path, "rb") as f:
            step_data = f.read()
        os.remove(tmp_step_path)
        st.download_button(_( "download_step" ), data=step_data, file_name="brick_brick.step", mime="application/x-step")

if __name__ == "__main__":
    # 启动 Xvfb（若环境需要），仅在非 Windows 环境下调用
    if "IS_XVFB_RUNNING" not in st.session_state:
        if sys.platform != "win32":
            from stpyvista.utils import start_xvfb
            start_xvfb()
        st.session_state["IS_XVFB_RUNNING"] = True
    main()
