#include "acados_pifire.h"

#include <float.h>
#include <math.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

#include "acados/utils/types.h"
#include "acados_c/ocp_nlp_interface.h"
#include "acados_solver_pifire_grey.h"

#define GREY_NX PIFIRE_GREY_NX
#define GREY_NU PIFIRE_GREY_NU
#define GREY_NP PIFIRE_GREY_NP
#define GREY_MAX_LAM (2 * GREY_NX + 2)
#define GREY_FEASIBILITY_TOLERANCE 1e-6

#if PIFIRE_GREY_N != ACADOS_PIFIRE_GREY_HORIZON_CAPACITY
#error "Generated grey solver horizon must match ABI capacity"
#endif
#if PIFIRE_GREY_NX != ACADOS_PIFIRE_GREY_GENERATED_STATE_SIZE
#error "Generated grey solver state width must match ABI"
#endif
#if PIFIRE_GREY_NU != 1
#error "Generated grey solver must have exactly one control"
#endif
#if PIFIRE_GREY_NP != 12
#error "Generated grey solver parameter width must match ABI"
#endif

struct acados_pifire_grey_handle {
    pifire_grey_solver_capsule *capsule;
    acados_pifire_grey_config config;
    int horizon_steps;
    int has_warm_start;
    double *successful_x;
    double *successful_u;
    double *successful_pi;
    double *successful_lam;
    int *successful_lam_dims;
};

static int finite_number(double value)
{
    return isfinite(value);
}

static int valid_config(const acados_pifire_grey_config *config)
{
    if (config == NULL || config->struct_size != sizeof(*config)) {
        return 0;
    }
    if (config->horizon_steps < ACADOS_PIFIRE_GREY_MIN_HORIZON
        || config->horizon_steps > ACADOS_PIFIRE_GREY_HORIZON_CAPACITY) {
        return 0;
    }
    if (!finite_number(config->C_c) || config->C_c <= 0.0
        || !finite_number(config->h_amb) || config->h_amb < 0.0
        || !finite_number(config->T_amb)
        || !finite_number(config->theta) || config->theta <= 0.0
        || !finite_number(config->K_Q) || config->K_Q <= 0.0
        || !finite_number(config->sigma) || config->sigma < 0.0
        || !finite_number(config->temperature_weight)
        || config->temperature_weight < 0.0
        || !finite_number(config->terminal_weight)
        || config->terminal_weight < 0.0
        || !finite_number(config->move_weight) || config->move_weight < 0.0
        || !finite_number(config->residual_weight)
        || config->residual_weight < 0.0) {
        return 0;
    }
    if (config->temperature_weight == 0.0 && config->terminal_weight == 0.0
        && config->move_weight == 0.0 && config->residual_weight == 0.0) {
        return 0;
    }
    return config->max_iterations > 0
        && config->max_iterations <= ACADOS_PIFIRE_GREY_MAX_ITERATIONS;
}

static int valid_input(const acados_pifire_grey_solve_input *input)
{
    int index;
    if (input == NULL || input->struct_size != sizeof(*input)) {
        return 0;
    }
    for (index = 0; index < ACADOS_PIFIRE_GREY_STATE_SIZE; ++index) {
        if (!finite_number(input->state[index])) {
            return 0;
        }
    }
    return finite_number(input->setpoint_c)
        && finite_number(input->q_previous)
        && input->q_previous >= 0.0
        && input->q_previous <= 1.0
        && finite_number(input->equilibrium_q)
        && input->equilibrium_q >= 0.0
        && input->equilibrium_q <= 1.0;
}

static double finite_or_max(double value)
{
    return finite_number(value) && value >= 0.0 ? value : DBL_MAX;
}

static void initialize_output(
    acados_pifire_grey_solve_output *output,
    int warm_started)
{
    uint32_t output_size = output->struct_size;
    memset(output, 0, sizeof(*output));
    output->struct_size = output_size;
    output->diagnostics.struct_size = sizeof(output->diagnostics);
    output->diagnostics.status = ACADOS_PIFIRE_STATUS_BACKEND_FAILURE;
    output->diagnostics.backend_status = -1;
    output->diagnostics.warm_started = warm_started;
}

static int allocate_warm_buffers(acados_pifire_grey_handle *handle)
{
    size_t stages = (size_t) handle->horizon_steps;
    handle->successful_x = calloc((stages + 1) * GREY_NX, sizeof(double));
    handle->successful_u = calloc(stages * GREY_NU, sizeof(double));
    handle->successful_pi = calloc(stages * GREY_NX, sizeof(double));
    handle->successful_lam = calloc(
        (stages + 1) * GREY_MAX_LAM,
        sizeof(double));
    handle->successful_lam_dims = calloc(stages + 1, sizeof(int));
    return handle->successful_x != NULL
        && handle->successful_u != NULL
        && handle->successful_pi != NULL
        && handle->successful_lam != NULL
        && handle->successful_lam_dims != NULL;
}

static void free_warm_buffers(acados_pifire_grey_handle *handle)
{
    free(handle->successful_x);
    free(handle->successful_u);
    free(handle->successful_pi);
    free(handle->successful_lam);
    free(handle->successful_lam_dims);
    handle->successful_x = NULL;
    handle->successful_u = NULL;
    handle->successful_pi = NULL;
    handle->successful_lam = NULL;
    handle->successful_lam_dims = NULL;
}

static double *saved_x(acados_pifire_grey_handle *handle, int stage)
{
    return handle->successful_x + (size_t) stage * GREY_NX;
}

static double *saved_u(acados_pifire_grey_handle *handle, int stage)
{
    return handle->successful_u + (size_t) stage * GREY_NU;
}

static double *saved_pi(acados_pifire_grey_handle *handle, int stage)
{
    return handle->successful_pi + (size_t) stage * GREY_NX;
}

static double *saved_lam(acados_pifire_grey_handle *handle, int stage)
{
    return handle->successful_lam + (size_t) stage * GREY_MAX_LAM;
}

static int capture_lam_dimensions(acados_pifire_grey_handle *handle)
{
    pifire_grey_solver_capsule *capsule = handle->capsule;
    int stage;
    for (stage = 0; stage <= handle->horizon_steps; ++stage) {
        int dimension = ocp_nlp_dims_get_from_attr(
            capsule->nlp_config,
            capsule->nlp_dims,
            capsule->nlp_out,
            stage,
            "lam");
        if (dimension < 0 || dimension > GREY_MAX_LAM) {
            return 0;
        }
        handle->successful_lam_dims[stage] = dimension;
    }
    return 1;
}

static void set_initial_state(
    acados_pifire_grey_handle *handle,
    const double state[GREY_NX])
{
    pifire_grey_solver_capsule *capsule = handle->capsule;
    ocp_nlp_constraints_model_set(
        capsule->nlp_config,
        capsule->nlp_dims,
        capsule->nlp_in,
        capsule->nlp_out,
        0,
        "lbx",
        (void *) state);
    ocp_nlp_constraints_model_set(
        capsule->nlp_config,
        capsule->nlp_dims,
        capsule->nlp_in,
        capsule->nlp_out,
        0,
        "ubx",
        (void *) state);
}

static void set_cold_guess(
    acados_pifire_grey_handle *handle,
    const double state[GREY_NX])
{
    pifire_grey_solver_capsule *capsule = handle->capsule;
    double residual[GREY_NU] = {state[GREY_NX - 1]};
    int stage;

    for (stage = 0; stage < handle->horizon_steps; ++stage) {
        ocp_nlp_out_set(
            capsule->nlp_config,
            capsule->nlp_dims,
            capsule->nlp_out,
            capsule->nlp_in,
            stage,
            "x",
            (void *) state);
        ocp_nlp_out_set(
            capsule->nlp_config,
            capsule->nlp_dims,
            capsule->nlp_out,
            capsule->nlp_in,
            stage,
            "u",
            residual);
    }
    ocp_nlp_out_set(
        capsule->nlp_config,
        capsule->nlp_dims,
        capsule->nlp_out,
        capsule->nlp_in,
        handle->horizon_steps,
        "x",
        (void *) state);
}

static void save_successful_iterate(acados_pifire_grey_handle *handle)
{
    pifire_grey_solver_capsule *capsule = handle->capsule;
    int stage;

    for (stage = 0; stage <= handle->horizon_steps; ++stage) {
        int lam_dimension = handle->successful_lam_dims[stage];
        ocp_nlp_out_get(
            capsule->nlp_config,
            capsule->nlp_dims,
            capsule->nlp_out,
            stage,
            "x",
            saved_x(handle, stage));
        if (lam_dimension > 0) {
            ocp_nlp_out_get(
                capsule->nlp_config,
                capsule->nlp_dims,
                capsule->nlp_out,
                stage,
                "lam",
                saved_lam(handle, stage));
        }
        if (stage < handle->horizon_steps) {
            ocp_nlp_out_get(
                capsule->nlp_config,
                capsule->nlp_dims,
                capsule->nlp_out,
                stage,
                "u",
                saved_u(handle, stage));
            ocp_nlp_out_get(
                capsule->nlp_config,
                capsule->nlp_dims,
                capsule->nlp_out,
                stage,
                "pi",
                saved_pi(handle, stage));
        }
    }
}

static void restore_successful_iterate(acados_pifire_grey_handle *handle)
{
    pifire_grey_solver_capsule *capsule = handle->capsule;
    int stage;

    pifire_grey_acados_reset(capsule, 1, 0, 0, 0);
    for (stage = 0; stage <= handle->horizon_steps; ++stage) {
        int lam_dimension = handle->successful_lam_dims[stage];
        ocp_nlp_out_set(
            capsule->nlp_config,
            capsule->nlp_dims,
            capsule->nlp_out,
            capsule->nlp_in,
            stage,
            "x",
            saved_x(handle, stage));
        if (lam_dimension > 0) {
            ocp_nlp_out_set(
                capsule->nlp_config,
                capsule->nlp_dims,
                capsule->nlp_out,
                capsule->nlp_in,
                stage,
                "lam",
                saved_lam(handle, stage));
        }
        if (stage < handle->horizon_steps) {
            ocp_nlp_out_set(
                capsule->nlp_config,
                capsule->nlp_dims,
                capsule->nlp_out,
                capsule->nlp_in,
                stage,
                "u",
                saved_u(handle, stage));
            ocp_nlp_out_set(
                capsule->nlp_config,
                capsule->nlp_dims,
                capsule->nlp_out,
                capsule->nlp_in,
                stage,
                "pi",
                saved_pi(handle, stage));
        }
    }
}

static void restore_after_failure(acados_pifire_grey_handle *handle)
{
    if (handle->has_warm_start) {
        restore_successful_iterate(handle);
    } else {
        pifire_grey_acados_reset(handle->capsule, 1, 0, 0, 0);
    }
}

static void release_handle(acados_pifire_grey_handle *handle)
{
    if (handle == NULL) {
        return;
    }
    if (handle->capsule != NULL) {
        pifire_grey_acados_free(handle->capsule);
        pifire_grey_acados_free_capsule(handle->capsule);
        handle->capsule = NULL;
    }
    free_warm_buffers(handle);
    memset(handle, 0, sizeof(*handle));
    free(handle);
}

int32_t acados_pifire_grey_create(
    const acados_pifire_grey_config *config,
    acados_pifire_grey_handle **handle_out)
{
    acados_pifire_grey_handle *handle;
    double time_steps[ACADOS_PIFIRE_GREY_HORIZON_CAPACITY];
    double unit_scaling = 1.0;
    int backend_status;
    int stage;

    if (handle_out == NULL) {
        return ACADOS_PIFIRE_STATUS_INVALID_ARGUMENT;
    }
    *handle_out = NULL;
    if (config == NULL) {
        return ACADOS_PIFIRE_STATUS_INVALID_ARGUMENT;
    }
    if (config->struct_size != sizeof(*config)) {
        return ACADOS_PIFIRE_STATUS_STRUCT_SIZE_MISMATCH;
    }
    if (!valid_config(config)) {
        return ACADOS_PIFIRE_STATUS_INVALID_ARGUMENT;
    }

    handle = calloc(1, sizeof(*handle));
    if (handle == NULL) {
        return ACADOS_PIFIRE_STATUS_ALLOCATION_FAILURE;
    }
    handle->config = *config;
    handle->horizon_steps = (int) config->horizon_steps;
    if (!allocate_warm_buffers(handle)) {
        release_handle(handle);
        return ACADOS_PIFIRE_STATUS_ALLOCATION_FAILURE;
    }

    handle->capsule = pifire_grey_acados_create_capsule();
    if (handle->capsule == NULL) {
        release_handle(handle);
        return ACADOS_PIFIRE_STATUS_ALLOCATION_FAILURE;
    }
    for (stage = 0; stage < handle->horizon_steps; ++stage) {
        time_steps[stage] = ACADOS_PIFIRE_GREY_TIMESTEP_SECONDS;
    }
    backend_status = pifire_grey_acados_create_with_discretization(
        handle->capsule,
        handle->horizon_steps,
        time_steps);
    if (backend_status != ACADOS_SUCCESS) {
        release_handle(handle);
        return ACADOS_PIFIRE_STATUS_BACKEND_FAILURE;
    }

    for (stage = 0; stage < handle->horizon_steps; ++stage) {
        backend_status = ocp_nlp_cost_model_set(
            handle->capsule->nlp_config,
            handle->capsule->nlp_dims,
            handle->capsule->nlp_in,
            stage,
            "scaling",
            &unit_scaling);
        if (backend_status != ACADOS_SUCCESS) {
            release_handle(handle);
            return ACADOS_PIFIRE_STATUS_BACKEND_FAILURE;
        }
    }
    if (!capture_lam_dimensions(handle)) {
        release_handle(handle);
        return ACADOS_PIFIRE_STATUS_BACKEND_FAILURE;
    }
    ocp_nlp_solver_opts_set(
        handle->capsule->nlp_config,
        handle->capsule->nlp_opts,
        "max_iter",
        &handle->config.max_iterations);

    *handle_out = handle;
    return ACADOS_PIFIRE_STATUS_SUCCESS;
}

int32_t acados_pifire_grey_solve(
    acados_pifire_grey_handle *handle,
    const acados_pifire_grey_solve_input *input,
    acados_pifire_grey_solve_output *output)
{
    pifire_grey_solver_capsule *capsule;
    double parameters[GREY_NP];
    double initial_state[GREY_NX];
    double objective = 0.0;
    double kkt_residual = 0.0;
    double constraint_residual = 0.0;
    double solve_time = 0.0;
    int iterations = 0;
    int backend_status;
    int status;
    int stage;
    int warm_started;
    int solution_is_finite = 1;

    if (output == NULL) {
        return ACADOS_PIFIRE_STATUS_INVALID_ARGUMENT;
    }
    if (output->struct_size != sizeof(*output)) {
        return ACADOS_PIFIRE_STATUS_STRUCT_SIZE_MISMATCH;
    }

    warm_started = handle == NULL ? 0 : handle->has_warm_start;
    initialize_output(output, warm_started);
    if (handle == NULL || input == NULL) {
        output->diagnostics.status = ACADOS_PIFIRE_STATUS_INVALID_ARGUMENT;
        return ACADOS_PIFIRE_STATUS_INVALID_ARGUMENT;
    }
    if (input->struct_size != sizeof(*input)) {
        output->diagnostics.status = ACADOS_PIFIRE_STATUS_STRUCT_SIZE_MISMATCH;
        return ACADOS_PIFIRE_STATUS_STRUCT_SIZE_MISMATCH;
    }
    if (!valid_input(input)) {
        output->diagnostics.status = ACADOS_PIFIRE_STATUS_INVALID_ARGUMENT;
        return ACADOS_PIFIRE_STATUS_INVALID_ARGUMENT;
    }

    capsule = handle->capsule;
    parameters[0] = handle->config.C_c;
    parameters[1] = handle->config.h_amb;
    parameters[2] = handle->config.T_amb;
    parameters[3] = handle->config.theta;
    parameters[4] = handle->config.K_Q;
    parameters[5] = handle->config.sigma;
    parameters[6] = input->setpoint_c;
    parameters[7] = input->equilibrium_q;
    parameters[8] = handle->config.temperature_weight;
    parameters[9] = handle->config.terminal_weight;
    parameters[10] = handle->config.move_weight;
    parameters[11] = handle->config.residual_weight;
    for (stage = 0; stage <= handle->horizon_steps; ++stage) {
        pifire_grey_acados_update_params(capsule, stage, parameters, GREY_NP);
    }

    memcpy(initial_state, input->state, sizeof(input->state));
    initial_state[GREY_NX - 1] = input->q_previous - input->equilibrium_q;
    set_initial_state(handle, initial_state);
    if (!warm_started) {
        set_cold_guess(handle, initial_state);
    }

    backend_status = pifire_grey_acados_solve(capsule);
    ocp_nlp_get(capsule->nlp_solver, "time_tot", &solve_time);
    ocp_nlp_get(capsule->nlp_solver, "sqp_iter", &iterations);
    ocp_nlp_out_get(
        capsule->nlp_config,
        capsule->nlp_dims,
        capsule->nlp_out,
        0,
        "kkt_norm_inf",
        &kkt_residual);

    for (stage = 0; stage < handle->horizon_steps; ++stage) {
        double residual = 0.0;
        double total_load;
        double violation;
        ocp_nlp_out_get(
            capsule->nlp_config,
            capsule->nlp_dims,
            capsule->nlp_out,
            stage,
            "u",
            &residual);
        total_load = input->equilibrium_q + residual;
        output->sequence_residual[stage] = residual;
        output->sequence_q[stage] = total_load;
        if (!finite_number(residual) || !finite_number(total_load)) {
            solution_is_finite = 0;
            continue;
        }
        violation = total_load < 0.0 ? -total_load : 0.0;
        if (total_load > 1.0 && total_load - 1.0 > violation) {
            violation = total_load - 1.0;
        }
        if (violation > constraint_residual) {
            constraint_residual = violation;
        }
    }

    ocp_nlp_eval_cost(capsule->nlp_solver, capsule->nlp_in, capsule->nlp_out);
    ocp_nlp_get(capsule->nlp_solver, "cost_value", &objective);
    if (!finite_number(objective) || !finite_number(kkt_residual)
        || !finite_number(solve_time)) {
        solution_is_finite = 0;
    }

    status = ACADOS_PIFIRE_STATUS_SUCCESS;
    if (backend_status != ACADOS_SUCCESS) {
        status = ACADOS_PIFIRE_STATUS_BACKEND_FAILURE;
    } else if (!solution_is_finite
        || constraint_residual > GREY_FEASIBILITY_TOLERANCE) {
        status = ACADOS_PIFIRE_STATUS_INVALID_SOLUTION;
    }

    output->diagnostics.status = status;
    output->diagnostics.backend_status = backend_status;
    output->diagnostics.iterations = iterations >= 0 ? iterations : 0;
    output->diagnostics.solve_time_s = finite_or_max(solve_time);
    output->diagnostics.objective = finite_number(objective) ? objective : 0.0;
    output->diagnostics.kkt_residual = finite_or_max(kkt_residual);
    output->diagnostics.constraint_residual = solution_is_finite
        ? constraint_residual : DBL_MAX;

    if (status == ACADOS_PIFIRE_STATUS_SUCCESS) {
        output->sequence_length = (uint32_t) handle->horizon_steps;
        output->objective = objective;
        save_successful_iterate(handle);
        handle->has_warm_start = 1;
    } else {
        memset(output->sequence_q, 0, sizeof(output->sequence_q));
        memset(output->sequence_residual, 0, sizeof(output->sequence_residual));
        output->sequence_length = 0;
        output->objective = 0.0;
        restore_after_failure(handle);
    }
    return status;
}

int32_t acados_pifire_grey_reset(acados_pifire_grey_handle *handle)
{
    int backend_status;
    if (handle == NULL) {
        return ACADOS_PIFIRE_STATUS_INVALID_ARGUMENT;
    }
    backend_status = pifire_grey_acados_reset(handle->capsule, 1, 0, 0, 0);
    if (backend_status != ACADOS_SUCCESS) {
        return ACADOS_PIFIRE_STATUS_BACKEND_FAILURE;
    }
    memset(
        handle->successful_x,
        0,
        (size_t) (handle->horizon_steps + 1) * GREY_NX * sizeof(double));
    memset(
        handle->successful_u,
        0,
        (size_t) handle->horizon_steps * GREY_NU * sizeof(double));
    memset(
        handle->successful_pi,
        0,
        (size_t) handle->horizon_steps * GREY_NX * sizeof(double));
    memset(
        handle->successful_lam,
        0,
        (size_t) (handle->horizon_steps + 1) * GREY_MAX_LAM * sizeof(double));
    handle->has_warm_start = 0;
    return ACADOS_PIFIRE_STATUS_SUCCESS;
}

void acados_pifire_grey_destroy(acados_pifire_grey_handle *handle)
{
    release_handle(handle);
}
