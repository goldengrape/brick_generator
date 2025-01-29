import streamlit as st
import cadquery as cq
from cadquery import exporters
import tempfile
import os
import pyvista as pv
from stpyvista import stpyvista

# L_brick常用尺寸
UNIT_LENGTH = 8.0         # 每粒L_brick在 X/Y 上的长度
PLATE_HEIGHT = 3.2        # 1 plate 高度
ROOF_THICKNESS = 1.0      # 顶部保留的厚度
WALL_THICKNESS = 1.5      # 侧壁厚度
STUD_DIAMETER = 4.8       # 突起圆柱直径
STUD_HEIGHT = 1.8         # 突起圆柱高度
UNDERTUBE_OUTER_DIAM = 6.41  # 底部管外径
UNDERTUBE_INNER_DIAM = 4.8   # 底部管内径
PLAY = 0.2                # 公差/装配间隙

def build_brick(
    brick_length=3,  # 砖块长度(以L_brick粒为单位)
    brick_width=2,   # 砖块宽度
    brick_height=3,  # 砖块高度(3=标准砖, 1=单片plate)
    with_studs=True, # 是否需要顶部圆柱
    tolerance=0.0    # 在尺寸上额外加减的公差
):
    # --- 1) 外部完整实体 ---
    outer_length = brick_length * UNIT_LENGTH
    outer_width  = brick_width  * UNIT_LENGTH
    outer_height = brick_height * PLATE_HEIGHT

    base = (
        cq.Workplane("XY")
        .box(outer_length, outer_width, outer_height)
        .translate((0,0,outer_height/2))
    )

    # --- 2) 挖出内部空间，但保留顶部 ROOF_THICKNESS 厚度 ---
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

    # --- 3) 顶部圆柱 studs ---
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

    # --- 4) 底部管状 hollow under-tubes(可选) ---
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

    # --- 5) 合并所有几何体 ---
    brick = base
    if studs:
        brick = brick.union(studs)
    if under_tubes:
        brick = brick.union(under_tubes)

    # --- 6) 平移, 让砖块居中到原点 (可选) ---
    brick = brick.translate((shift_x, shift_y, 0))
    return brick


def main():
    st.title("Brick Generator")

    # 确保 session_state 中有一个计数器 count
    if "generate_count" not in st.session_state:
        st.session_state["generate_count"] = 0

    # 用 Streamlit 表单收集参数
    with st.sidebar.form("param_form"):
        brick_length = st.slider("length (units: studs)", 1, 48, 3)
        brick_width = st.slider("width (units: studs)", 1, 48, 2)
        brick_height = st.slider("height (1=plate, 3=brick)", 1, 10, 3)
        with_studs_opt = st.selectbox("studs？", ["yes", "no"], index=0)
        tolerance = st.number_input("tolerance (mm)", value=0.0, step=0.01)

        generate_button = st.form_submit_button(label="Generate")

    # 如果表单未提交就先停止，让用户先点“Generate”
    if not generate_button:
        st.stop()

    # 用户点击了 Generate，计数器加 1
    st.session_state["generate_count"] += 1

    with_studs = (with_studs_opt == "yes")

    # 生成 3D 模型
    brick_model = build_brick(
        brick_length=brick_length,
        brick_width=brick_width,
        brick_height=brick_height,
        with_studs=with_studs,
        tolerance=tolerance
    )

    # 把 CadQuery 结果导出为临时 STL，再用 PyVista 读取
    with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as tmp_stl:
        tmp_stl_path = tmp_stl.name
    exporters.export(brick_model, tmp_stl_path, exporters.ExportTypes.STL)
    mesh = pv.read(tmp_stl_path)
    os.remove(tmp_stl_path)

    # ---------------------------
    # 可交互的 3D 展示 (stpyvista)
    # 这里的 key 拼上 generate_count，保证每次都强制重新加载
    # ---------------------------
    plotter = pv.Plotter(window_size=(600, 500))  
    plotter.add_mesh(mesh, color="orange", show_edges=False)
    plotter.view_isometric()
    stpyvista(plotter, key=f"interactive_brick_{st.session_state['generate_count']}")

    # ---------------------------
    # 文件下载
    # ---------------------------
    with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as tmp_stl:
        tmp_stl_path = tmp_stl.name
    exporters.export(brick_model, tmp_stl_path, exporters.ExportTypes.STL)
    with open(tmp_stl_path, "rb") as f:
        stl_data = f.read()
    os.remove(tmp_stl_path)

    st.download_button(
        label="下载 STL 文件",
        data=stl_data,
        file_name="brick_brick.stl",
        mime="application/vnd.ms-pki.stl"
    )

    with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as tmp_step:
        tmp_step_path = tmp_step.name
    exporters.export(brick_model, tmp_step_path, exporters.ExportTypes.STEP)
    with open(tmp_step_path, "rb") as f:
        step_data = f.read()
    os.remove(tmp_step_path)

    st.download_button(
        label="下载 STEP 文件",
        data=step_data,
        file_name="brick_brick.step",
        mime="application/x-step"
    )


if __name__ == "__main__":
    main()
