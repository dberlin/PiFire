cmake_minimum_required(VERSION 3.18)

project(acados_pifire LANGUAGES C CXX)

include(FetchContent)
include(cmake/AcadosPifirePlatform.cmake)

set(BLASFEO_TARGET "${ACADOS_PIFIRE_BLASFEO_TARGET}" CACHE STRING
    "BLASFEO target architecture" FORCE)
set(HPIPM_TARGET "${ACADOS_PIFIRE_HPIPM_TARGET}" CACHE STRING
    "HPIPM target architecture" FORCE)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "Build dependencies as static libraries" FORCE)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(BLASFEO_EXAMPLES OFF CACHE BOOL "Disable BLASFEO examples" FORCE)
set(ACADOS_WITH_OPENMP ON CACHE BOOL "Enable acados OpenMP support" FORCE)
set(ACADOS_EXAMPLES OFF CACHE BOOL "Disable acados examples" FORCE)
set(ACADOS_UNIT_TESTS OFF CACHE BOOL "Disable acados unit tests" FORCE)

FetchContent_Declare(acados
    GIT_REPOSITORY https://github.com/acados/acados.git
    GIT_TAG 503364817c872d474ab5bed219c26760ac267769
    GIT_SHALLOW FALSE
    GIT_SUBMODULES_RECURSE TRUE
)
FetchContent_MakeAvailable(acados)

set(ACADOS_PIFIRE_ACADOS_SOURCE_DIR "${acados_SOURCE_DIR}" CACHE PATH
    "Resolved FetchContent acados source directory" FORCE)
set(ACADOS_PIFIRE_ACADOS_BINARY_DIR "${acados_BINARY_DIR}" CACHE PATH
    "Resolved FetchContent acados build directory" FORCE)
file(WRITE "${CMAKE_BINARY_DIR}/acados-source-dir.txt"
    "${ACADOS_PIFIRE_ACADOS_SOURCE_DIR}\n")

add_subdirectory(native)
