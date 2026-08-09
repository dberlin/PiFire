set(ACADOS_PIFIRE_GENERATED_ROOT
    "${CMAKE_CURRENT_SOURCE_DIR}/generated"
    CACHE PATH "Generated solver source root")
set(ACADOS_PIFIRE_LIBRARY_OUTPUT_DIRECTORY
    "${CMAKE_BINARY_DIR}/native-output"
    CACHE PATH "Native wrapper library output directory")

set(ACADOS_PIFIRE_GREY_GENERATED
    ${ACADOS_PIFIRE_GENERATED_ROOT}/grey_box/acados_solver_pifire_grey.c
    ${ACADOS_PIFIRE_GENERATED_ROOT}/grey_box/pifire_grey_constraints/pifire_grey_constr_h_0_fun.c
    ${ACADOS_PIFIRE_GENERATED_ROOT}/grey_box/pifire_grey_constraints/pifire_grey_constr_h_0_fun_jac_uxt_zt.c
    ${ACADOS_PIFIRE_GENERATED_ROOT}/grey_box/pifire_grey_constraints/pifire_grey_constr_h_fun.c
    ${ACADOS_PIFIRE_GENERATED_ROOT}/grey_box/pifire_grey_constraints/pifire_grey_constr_h_fun_jac_uxt_zt.c
    ${ACADOS_PIFIRE_GENERATED_ROOT}/grey_box/pifire_grey_cost/pifire_grey_cost_y_0_fun.c
    ${ACADOS_PIFIRE_GENERATED_ROOT}/grey_box/pifire_grey_cost/pifire_grey_cost_y_0_fun_jac_ut_xt.c
    ${ACADOS_PIFIRE_GENERATED_ROOT}/grey_box/pifire_grey_cost/pifire_grey_cost_y_e_fun.c
    ${ACADOS_PIFIRE_GENERATED_ROOT}/grey_box/pifire_grey_cost/pifire_grey_cost_y_e_fun_jac_ut_xt.c
    ${ACADOS_PIFIRE_GENERATED_ROOT}/grey_box/pifire_grey_cost/pifire_grey_cost_y_fun.c
    ${ACADOS_PIFIRE_GENERATED_ROOT}/grey_box/pifire_grey_cost/pifire_grey_cost_y_fun_jac_ut_xt.c
    ${ACADOS_PIFIRE_GENERATED_ROOT}/grey_box/pifire_grey_model/pifire_grey_dyn_disc_phi_fun.c
    ${ACADOS_PIFIRE_GENERATED_ROOT}/grey_box/pifire_grey_model/pifire_grey_dyn_disc_phi_fun_jac.c
)

add_library(acados_pifire SHARED
    src/common.c
    src/grey_box.c
    ${ACADOS_PIFIRE_GREY_GENERATED}
)

target_include_directories(acados_pifire
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    PRIVATE
        ${ACADOS_PIFIRE_GENERATED_ROOT}/grey_box
)
target_link_libraries(acados_pifire PRIVATE acados)
include("${CMAKE_CURRENT_SOURCE_DIR}/AcadosPifireExports.cmake")
target_link_options(acados_pifire PRIVATE
    "${ACADOS_PIFIRE_EXPORT_LINK_OPTION}")
set_property(TARGET acados_pifire APPEND PROPERTY LINK_DEPENDS
    "${ACADOS_PIFIRE_EXPORT_CONTROL_FILE}")

set_target_properties(acados_pifire PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${ACADOS_PIFIRE_LIBRARY_OUTPUT_DIRECTORY}"
    RUNTIME_OUTPUT_DIRECTORY "${ACADOS_PIFIRE_LIBRARY_OUTPUT_DIRECTORY}"
    C_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN YES
)
