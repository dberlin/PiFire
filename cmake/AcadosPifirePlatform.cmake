string(TOLOWER "${CMAKE_SYSTEM_PROCESSOR}" ACADOS_PIFIRE_PROCESSOR)

if(NOT DEFINED ACADOS_PIFIRE_AVX_AVAILABLE)
  if(ACADOS_PIFIRE_PROCESSOR MATCHES "^(x86_64|amd64)$"
     AND NOT CMAKE_CROSSCOMPILING)
    include(CheckCSourceRuns)
    check_c_source_runs(
      "int main(void) {
      #if defined(__GNUC__) || defined(__clang__)
        __builtin_cpu_init();
        return __builtin_cpu_supports(\"avx\") ? 0 : 1;
      #else
        return 1;
      #endif
      }"
      ACADOS_PIFIRE_AVX_AVAILABLE)
  else()
    set(ACADOS_PIFIRE_AVX_AVAILABLE OFF)
  endif()
endif()

if(CMAKE_SYSTEM_NAME STREQUAL "Darwin")
  if(ACADOS_PIFIRE_PROCESSOR MATCHES "^(arm64|aarch64)$")
    set(ACADOS_PIFIRE_BLASFEO_TARGET "ARMV8A_APPLE_M1")
    set(ACADOS_PIFIRE_HPIPM_TARGET "GENERIC")
  elseif(ACADOS_PIFIRE_PROCESSOR MATCHES "^(x86_64|amd64)$")
    set(ACADOS_PIFIRE_BLASFEO_TARGET "X64_AUTOMATIC")
    if(ACADOS_PIFIRE_AVX_AVAILABLE)
      set(ACADOS_PIFIRE_HPIPM_TARGET "AVX")
    else()
      set(ACADOS_PIFIRE_HPIPM_TARGET "GENERIC")
    endif()
  else()
    message(FATAL_ERROR
            "Unsupported Apple processor for acados-pifire: ${CMAKE_SYSTEM_PROCESSOR}")
  endif()
elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  if(ACADOS_PIFIRE_PROCESSOR MATCHES "^(x86_64|amd64)$")
    set(ACADOS_PIFIRE_BLASFEO_TARGET "X64_AUTOMATIC")
    if(ACADOS_PIFIRE_AVX_AVAILABLE)
      set(ACADOS_PIFIRE_HPIPM_TARGET "AVX")
    else()
      set(ACADOS_PIFIRE_HPIPM_TARGET "GENERIC")
    endif()
  elseif(ACADOS_PIFIRE_PROCESSOR MATCHES "^(arm64|aarch64)$")
    set(ACADOS_PIFIRE_BLASFEO_TARGET "ARMV8A_ARM_CORTEX_A57")
    set(ACADOS_PIFIRE_HPIPM_TARGET "GENERIC")
  else()
    message(FATAL_ERROR
            "Unsupported Linux processor for acados-pifire: ${CMAKE_SYSTEM_PROCESSOR}")
  endif()
else()
  message(FATAL_ERROR
        "Unsupported host for acados-pifire: ${CMAKE_SYSTEM_NAME}/${CMAKE_SYSTEM_PROCESSOR}")
endif()
