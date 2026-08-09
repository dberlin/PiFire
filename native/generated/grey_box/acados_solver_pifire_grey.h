/*
 * Copyright (c) The acados authors.
 *
 * This file is part of acados.
 *
 * The 2-Clause BSD License
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 * this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 * this list of conditions and the following disclaimer in the documentation
 * and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
 * LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
 * CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.;
 */

#ifndef ACADOS_SOLVER_pifire_grey_H_
#define ACADOS_SOLVER_pifire_grey_H_

#include "acados/utils/types.h"

#include "acados_c/ocp_nlp_interface.h"
#include "acados_c/external_function_interface.h"

#define PIFIRE_GREY_NX     11
#define PIFIRE_GREY_NZ     0
#define PIFIRE_GREY_NU     1
#define PIFIRE_GREY_NP     12
#define PIFIRE_GREY_NP_GLOBAL     0
#define PIFIRE_GREY_NBX    0
#define PIFIRE_GREY_NBX0   11
#define PIFIRE_GREY_NBU    0
#define PIFIRE_GREY_NSBX   0
#define PIFIRE_GREY_NSBU   0
#define PIFIRE_GREY_NSH    0
#define PIFIRE_GREY_NSH0   0
#define PIFIRE_GREY_NSG    0
#define PIFIRE_GREY_NSPHI  0
#define PIFIRE_GREY_NSHN   0
#define PIFIRE_GREY_NSGN   0
#define PIFIRE_GREY_NSPHIN 0
#define PIFIRE_GREY_NSPHI0 0
#define PIFIRE_GREY_NSBXN  0
#define PIFIRE_GREY_NS     0
#define PIFIRE_GREY_NS0    0
#define PIFIRE_GREY_NSN    0
#define PIFIRE_GREY_NG     0
#define PIFIRE_GREY_NBXN   0
#define PIFIRE_GREY_NGN    0
#define PIFIRE_GREY_NY0    3
#define PIFIRE_GREY_NY     3
#define PIFIRE_GREY_NYN    1
#define PIFIRE_GREY_N      24
#define PIFIRE_GREY_NH     1
#define PIFIRE_GREY_NHN    0
#define PIFIRE_GREY_NH0    1
#define PIFIRE_GREY_NPHI0  0
#define PIFIRE_GREY_NPHI   0
#define PIFIRE_GREY_NPHIN  0
#define PIFIRE_GREY_NR     0

#ifdef __cplusplus
extern "C" {
#endif


// ** capsule for solver data **
typedef struct pifire_grey_solver_capsule
{
    // acados objects
    ocp_nlp_in *nlp_in;
    ocp_nlp_out *nlp_out;
    ocp_nlp_out *sens_out;
    ocp_nlp_solver *nlp_solver;
    void *nlp_opts;
    ocp_nlp_plan_t *nlp_solver_plan;
    ocp_nlp_config *nlp_config;
    ocp_nlp_dims *nlp_dims;

    // number of expected runtime parameters
    unsigned int nlp_np;

    /* external functions */

    // dynamics

    external_function_external_param_casadi *discr_dyn_phi_fun;
    external_function_external_param_casadi *discr_dyn_phi_fun_jac_ut_xt;





    // cost

    external_function_external_param_casadi *cost_y_fun;
    external_function_external_param_casadi *cost_y_fun_jac_ut_xt;



    external_function_external_param_casadi cost_y_0_fun;
    external_function_external_param_casadi cost_y_0_fun_jac_ut_xt;



    external_function_external_param_casadi cost_y_e_fun;
    external_function_external_param_casadi cost_y_e_fun_jac_ut_xt;


    // constraints
    external_function_external_param_casadi *nl_constr_h_fun_jac;
    external_function_external_param_casadi *nl_constr_h_fun;






    external_function_external_param_casadi nl_constr_h_0_fun_jac;
    external_function_external_param_casadi nl_constr_h_0_fun;







} pifire_grey_solver_capsule;

ACADOS_SYMBOL_EXPORT pifire_grey_solver_capsule * pifire_grey_acados_create_capsule(void);
ACADOS_SYMBOL_EXPORT int pifire_grey_acados_free_capsule(pifire_grey_solver_capsule *capsule);

ACADOS_SYMBOL_EXPORT int pifire_grey_acados_create(pifire_grey_solver_capsule * capsule);

ACADOS_SYMBOL_EXPORT int pifire_grey_acados_reset(pifire_grey_solver_capsule* capsule, int reset_qp_solver_mem, int reset_numerical_values, int reset_solver_options, int reset_x_to_x0_bar);

/**
 * Generic version of pifire_grey_acados_create which allows to use a different number of shooting intervals than
 * the number used for code generation. If new_time_steps=NULL and n_time_steps matches the number used for code
 * generation, the time-steps from code generation is used.
 */
ACADOS_SYMBOL_EXPORT int pifire_grey_acados_create_with_discretization(pifire_grey_solver_capsule * capsule, int n_time_steps, double* new_time_steps);
/**
 * Update the time step vector. Number N must be identical to the currently set number of shooting nodes in the
 * nlp_solver_plan. Returns 0 if no error occurred and a otherwise a value other than 0.
 */
ACADOS_SYMBOL_EXPORT int pifire_grey_acados_update_time_steps(pifire_grey_solver_capsule * capsule, int N, double* new_time_steps);
/**
 * This function is used for updating an already initialized solver with a different number of qp_cond_N.
 */
ACADOS_SYMBOL_EXPORT int pifire_grey_acados_update_qp_solver_cond_N(pifire_grey_solver_capsule * capsule, int qp_solver_cond_N);
ACADOS_SYMBOL_EXPORT int pifire_grey_acados_update_params(pifire_grey_solver_capsule * capsule, int stage, double *value, int np);
ACADOS_SYMBOL_EXPORT int pifire_grey_acados_update_params_sparse(pifire_grey_solver_capsule * capsule, int stage, int *idx, double *p, int n_update);
ACADOS_SYMBOL_EXPORT int pifire_grey_acados_set_p_global_and_precompute_dependencies(pifire_grey_solver_capsule* capsule, double* data, int data_len);

ACADOS_SYMBOL_EXPORT int pifire_grey_acados_solve(pifire_grey_solver_capsule * capsule);
ACADOS_SYMBOL_EXPORT int pifire_grey_acados_setup_qp_matrices_and_factorize(pifire_grey_solver_capsule* capsule);



ACADOS_SYMBOL_EXPORT int pifire_grey_acados_free(pifire_grey_solver_capsule * capsule);
ACADOS_SYMBOL_EXPORT void pifire_grey_acados_print_stats(pifire_grey_solver_capsule * capsule);
ACADOS_SYMBOL_EXPORT int pifire_grey_acados_custom_update(pifire_grey_solver_capsule* capsule, double* data, int data_len);

ACADOS_SYMBOL_EXPORT ocp_nlp_in *pifire_grey_acados_get_nlp_in(pifire_grey_solver_capsule * capsule);
ACADOS_SYMBOL_EXPORT ocp_nlp_out *pifire_grey_acados_get_nlp_out(pifire_grey_solver_capsule * capsule);
ACADOS_SYMBOL_EXPORT ocp_nlp_out *pifire_grey_acados_get_sens_out(pifire_grey_solver_capsule * capsule);
ACADOS_SYMBOL_EXPORT ocp_nlp_solver *pifire_grey_acados_get_nlp_solver(pifire_grey_solver_capsule * capsule);
ACADOS_SYMBOL_EXPORT ocp_nlp_config *pifire_grey_acados_get_nlp_config(pifire_grey_solver_capsule * capsule);
ACADOS_SYMBOL_EXPORT void *pifire_grey_acados_get_nlp_opts(pifire_grey_solver_capsule * capsule);
ACADOS_SYMBOL_EXPORT ocp_nlp_dims *pifire_grey_acados_get_nlp_dims(pifire_grey_solver_capsule * capsule);
ACADOS_SYMBOL_EXPORT ocp_nlp_plan_t *pifire_grey_acados_get_nlp_plan(pifire_grey_solver_capsule * capsule);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif  // ACADOS_SOLVER_pifire_grey_H_
