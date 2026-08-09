if(CMAKE_SYSTEM_NAME STREQUAL "Darwin")
    set(ACADOS_PIFIRE_EXPORT_CONTROL_FILE
        "${CMAKE_CURRENT_LIST_DIR}/acados_pifire.exports")
    set(ACADOS_PIFIRE_EXPORT_LINK_OPTION
        "LINKER:-exported_symbols_list,${ACADOS_PIFIRE_EXPORT_CONTROL_FILE}")
elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    set(ACADOS_PIFIRE_EXPORT_CONTROL_FILE
        "${CMAKE_CURRENT_LIST_DIR}/acados_pifire.version-script")
    set(ACADOS_PIFIRE_EXPORT_LINK_OPTION
        "LINKER:--version-script=${ACADOS_PIFIRE_EXPORT_CONTROL_FILE}")
else()
    message(FATAL_ERROR
        "Unsupported export control platform: ${CMAKE_SYSTEM_NAME}")
endif()
