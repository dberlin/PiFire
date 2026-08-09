#ifndef ACADOS_PIFIRE_H
#define ACADOS_PIFIRE_H

#if defined(_WIN32)
#define ACADOS_PIFIRE_EXPORT __declspec(dllexport)
#else
#define ACADOS_PIFIRE_EXPORT __attribute__((visibility("default")))
#endif

#define ACADOS_PIFIRE_ABI_VERSION 2

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define ACADOS_PIFIRE_GREY_DELAY_STATES 8
#define ACADOS_PIFIRE_GREY_STATE_SIZE 10
#define ACADOS_PIFIRE_GREY_GENERATED_STATE_SIZE 11
#define ACADOS_PIFIRE_GREY_MIN_HORIZON 5
#define ACADOS_PIFIRE_GREY_HORIZON_CAPACITY 24
#define ACADOS_PIFIRE_GREY_TIMESTEP_SECONDS 25.0
#define ACADOS_PIFIRE_GREY_MAX_ITERATIONS 10

typedef enum acados_pifire_status {
    ACADOS_PIFIRE_STATUS_SUCCESS = 0,
    ACADOS_PIFIRE_STATUS_INVALID_ARGUMENT = 1,
    ACADOS_PIFIRE_STATUS_STRUCT_SIZE_MISMATCH = 2,
    ACADOS_PIFIRE_STATUS_ALLOCATION_FAILURE = 3,
    ACADOS_PIFIRE_STATUS_BACKEND_FAILURE = 4,
    ACADOS_PIFIRE_STATUS_INVALID_SOLUTION = 5
} acados_pifire_status;

typedef struct acados_pifire_grey_handle acados_pifire_grey_handle;

typedef struct acados_pifire_grey_config {
    uint32_t struct_size;
    uint32_t horizon_steps;
    double C_c;
    double h_amb;
    double T_amb;
    double theta;
    double K_Q;
    double sigma;
    double temperature_weight;
    double terminal_weight;
    double move_weight;
    double residual_weight;
    int32_t max_iterations;
} acados_pifire_grey_config;

typedef struct acados_pifire_grey_solve_input {
    uint32_t struct_size;
    double state[ACADOS_PIFIRE_GREY_STATE_SIZE];
    double setpoint_c;
    double q_previous;
    double equilibrium_q;
} acados_pifire_grey_solve_input;

typedef struct acados_pifire_grey_diagnostics {
    uint32_t struct_size;
    int32_t status;
    int32_t backend_status;
    int32_t iterations;
    double solve_time_s;
    double objective;
    double kkt_residual;
    double constraint_residual;
    int32_t warm_started;
} acados_pifire_grey_diagnostics;

typedef struct acados_pifire_grey_solve_output {
    uint32_t struct_size;
    uint32_t sequence_length;
    double sequence_q[ACADOS_PIFIRE_GREY_HORIZON_CAPACITY];
    double sequence_residual[ACADOS_PIFIRE_GREY_HORIZON_CAPACITY];
    double objective;
    acados_pifire_grey_diagnostics diagnostics;
} acados_pifire_grey_solve_output;

ACADOS_PIFIRE_EXPORT int acados_pifire_abi_version(void);
ACADOS_PIFIRE_EXPORT int32_t acados_pifire_grey_create(
    const acados_pifire_grey_config *config,
    acados_pifire_grey_handle **handle_out);
ACADOS_PIFIRE_EXPORT void acados_pifire_grey_destroy(
    acados_pifire_grey_handle *handle);
ACADOS_PIFIRE_EXPORT int32_t acados_pifire_grey_reset(
    acados_pifire_grey_handle *handle);
ACADOS_PIFIRE_EXPORT int32_t acados_pifire_grey_solve(
    acados_pifire_grey_handle *handle,
    const acados_pifire_grey_solve_input *input,
    acados_pifire_grey_solve_output *output);

#ifdef __cplusplus
}
#endif

#endif
