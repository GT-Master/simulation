import sys
import argparse

import os
import numpy as np

import util.pattern
import util.io.fs

from ndop.optimization.matlab.constants import GLS_DICT
COST_FUNCTION_NAMES = ['setup_{setup}/{data_kind}/{cost_function}'.format(data_kind=dk, cost_function=cf, setup=setup) for dk in ('WOA', 'WOD', 'WOD_TMM_1', 'WOD_TMM_0') for cf in ('OLS', 'WLS', 'LWLS') for setup in (1, 2)] + ['setup_{setup}/{data_kind}/GLS/min_values_{min_values}/max_year_diff_inf/min_diag_1e-02'.format(data_kind=dk.replace('.', '_TMM_'), min_values=mv, setup=setup) for dk in GLS_DICT.keys() for mv in GLS_DICT[dk] for setup in (1, 2)]
COST_FUNCTION_NAMES.sort()

def min_cf_values():
    from ndop.model.constants import MODEL_OUTPUT_DIR, MODEL_TIME_STEP_DIRNAME, MODEL_PARAMETERS_FILENAME

    for cost_function_name in COST_FUNCTION_NAMES:
        COST_FUNCTION_OUTPUT_DIRNAME = 'cost_functions/' + cost_function_name
        COST_FUNCTION_F_FILENAME = 'f.txt'

        min_cf_value = float('inf')
        min_cf_parameter_set_dir = None
        min_cf_parameter_set_number = None
        min_cf_p_str = None

        time_step_dirname = MODEL_TIME_STEP_DIRNAME.format(1)
        time_step_dir = os.path.join(MODEL_OUTPUT_DIR, time_step_dirname)

        parameter_set_dirs = util.io.fs.get_dirs(time_step_dir)

        for parameter_set_dir in parameter_set_dirs:
            cost_function_output_path = os.path.join(parameter_set_dir, COST_FUNCTION_OUTPUT_DIRNAME)
            cost_function_f_file = os.path.join(cost_function_output_path, COST_FUNCTION_F_FILENAME)

            if os.path.exists(cost_function_f_file):
                cf_value = np.sum(np.loadtxt(cost_function_f_file))

                if cf_value < min_cf_value:
                    parameters_file = os.path.join(parameter_set_dir, MODEL_PARAMETERS_FILENAME)
                    p = np.loadtxt(parameters_file)

                    min_cf_value = cf_value
                    min_cf_parameter_set_dir = parameter_set_dir

                    min_cf_p_str = np.array_str(p, precision=2)
                    min_cf_p_str = min_cf_p_str.replace('\n', '').replace('\r', '')


        print('For {} has {} the min value {} with parameters:'.format(cost_function_name, min_cf_parameter_set_dir, min_cf_value))
        print('{}'.format(min_cf_p_str))




if __name__ == "__main__":
    min_cf_values()