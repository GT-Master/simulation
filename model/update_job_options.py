import os
import stat

import numpy as np

import simulation.model.job

import util.io.fs
import util.options
import util.logging
logger = util.logging.logger


## general update functions for job options

def update_job_options(update_function):
    from simulation.model.constants import MODEL_NAMES, DATABASE_OUTPUT_DIR, DATABASE_MODEL_DIRNAME, DATABASE_SPINUP_DIRNAME

    for model_name in MODEL_NAMES:
        model_dirname = DATABASE_MODEL_DIRNAME.format(model_name)
        model_dir = os.path.join(DATABASE_OUTPUT_DIR, model_dirname)
        
        for time_step_dir in util.io.fs.get_dirs(model_dir):
            parameter_set_dirs = util.io.fs.get_dirs(time_step_dir)
            logger.debug('{} parameter set dirs found in {}.'.format(len(parameter_set_dirs), time_step_dir))
    
            for parameter_set_dir in parameter_set_dirs:
                spinup_dir = os.path.join(parameter_set_dir, DATABASE_SPINUP_DIRNAME)
                update_job_options_in_run_dirs(spinup_dir, update_function)
    
                derivative_dir = os.path.join(parameter_set_dir, 'derivative')
                
                for step_size_dir in util.io.fs.get_dirs(derivative_dir):
                    for partial_derivative_dir in util.io.fs.get_dirs(step_size_dir):
                        update_job_options_in_run_dirs(partial_derivative_dir, update_function)
    


def update_job_options_in_run_dirs(run_dir_path, update_function):
    from simulation.model.constants import DATABASE_RUN_DIRNAME

    runs_len = len(util.io.fs.get_dirs(run_dir_path))

    for run in range(runs_len):
        run_dirname = DATABASE_RUN_DIRNAME.format(run)
        run_dir = os.path.join(run_dir_path, run_dirname)

        if os.path.exists(run_dir):
            update_job_options_in_job_options_dir(run_dir, update_function)


def update_job_options_in_job_options_dir(job_options_dir, update_function):
    logger.debug('Updating job options in {}.'.format(job_options_dir))

    options_file = os.path.join(job_options_dir, 'job_options.hdf5')

    util.io.fs.make_writable(options_file)
    update_function(job_options_dir)
    util.io.fs.make_read_only(options_file)


## specific update functions for job options

def update_output_dir():
    def update_function(job_options_dir):
        with simulation.model.job.Metos3D_Job(job_options_dir, force_load=True) as job:
            job.update_output_dir(job_options_dir)

    update_job_options(update_function)


def add_finished_file():
    def update_function(job_options_dir):
        options_file = os.path.join(job_options_dir, 'job_options.hdf5')
        with util.options.Options(options_file, mode='r') as options:
            try:
                options['/job/finished_file']
                print('Finished file option already there in job option file {}.'.format(options_file))
            except KeyError:
                finished_file = os.path.join(job_options_dir, 'finished.txt')
                options['/job/finished_file'] = finished_file
                print('Finished file option added to job option file {}.'.format(options_file))

    update_job_options(update_function)



def update_str_options(old_str, new_str):
    def update_function(job_options_dir):
        options_file = os.path.join(job_options_dir, 'job_options.hdf5')
        with util.options.Options(options_file) as options:
            options.replace_all_str_options(old_str, new_str)
    update_job_options(update_function)


def update_new_option_entries():
    def update_function(job_options_dir):
        options_file = os.path.join(job_options_dir, 'job_options.hdf5')
        
        with util.options.Options(options_file) as options:
            try:
                options['/metos3d/tracer_input_path']
                options['/metos3d/initial_concentrations']
            except KeyError:
                pass
            else:
                del options['/metos3d/initial_concentrations']
                print('Initial concentration option removed, since tracer input is available to job option file {}.'.format(options_file))
            
            try:
                options['/model/concentrations']
            except KeyError:
                pass
            else:
                del options['/model/concentrations']
                print('Concentrations option removed in job option file {}.'.format(options_file))
            
            try:
                options['/metos3d/initial_concentrations']
            except KeyError:
                try:
                    options['/metos3d/tracer_input_path']
                except KeyError:
                    options['/metos3d/initial_concentrations'] = np.array([2.17, 10**-4])
                    print('Initial concentration option added to job option file {}.'.format(options_file))
            
            try:
                options['/model/time_step_multiplier']
            except KeyError:
                options['/model/time_step_multiplier'] = 1
                print('time_step_multiplier option added to job option file {}.'.format(options_file))
            
            try:
                options['/model/time_steps_per_year']
            except KeyError:
                options['/model/time_steps_per_year'] = options['/model/time_step_count']
                del options['/model/time_step_count']
                print('time_steps_per_year option added to job option file {}.'.format(options_file))
                
            try:
                options['/model/tracer']
            except KeyError:
                options['/model/tracer'] = ['po4', 'dop']
                print('model tracer option added to job option file {}.'.format(options_file))
                
            try:
                options['/metos3d/po4_output_filename']
            except KeyError:
                pass
            else:
                del options['/metos3d/po4_output_filename']
                del options['/metos3d/dop_output_filename']
                options['/metos3d/output_filenames'] = ['{}_output.petsc'.format(tracer) for tracer in options['/model/tracer']]
                print('generic output filenames added to job option file {}.'.format(options_file))
                
            try:
                options['/metos3d/po4_input_filename']
            except KeyError:
                pass
            else:
                del options['/metos3d/po4_input_filename']
                del options['/metos3d/dop_input_filename']
                options['/metos3d/input_filenames'] = ['{}_input.petsc'.format(tracer) for tracer in options['/model/tracer']]
                print('generic input filenames added to job option file {}.'.format(options_file))
            
            try:
                options['/metos3d/tracer_input_path']
            except KeyError:
                pass
            else:
                del options['/metos3d/tracer_input_path']
                print('tracer input path added to job option file {}.'.format(options_file))

            try:
                options['/metos3d/tracer_input_dir']
            except KeyError:
                pass
            else:
                # try:
                #     options['/model/tracer_input_dir']
                # except KeyError:
                #     import simulation.constants
                #     input_tracer = os.path.join(options['/metos3d/tracer_input_dir'], options['/metos3d/input_filenames'][0])
                #     options['/model/tracer_input_dir'] = os.path.dirname(os.path.realpath(input_tracer)).replace(simulation.constants.SIMULATION_OUTPUT_DIR, '${{{}}}'.format(simulation.constants.SIMULATION_OUTPUT_DIR_ENV_NAME))
                #     print('Model tracer input path added to job option file {}.'.format(options_file))
                
                import simulation.constants
                input_tracer = os.path.join(options['/metos3d/tracer_input_dir'], options['/metos3d/input_filenames'][0])
                options['/model/tracer_input_dir'] = os.path.dirname(os.path.realpath(input_tracer)).replace(simulation.constants.SIMULATION_OUTPUT_DIR, '${{{}}}'.format(simulation.constants.SIMULATION_OUTPUT_DIR_ENV_NAME))
                print('Model tracer input path added to job option file {}.'.format(options_file))
            
            try:
                options['/model/parameters_file']
            except KeyError:
                pass
            else:
                del options['/model/parameters_file']
                print('Model parameters file removed in job option file {}.'.format(options_file))
            
            try:
                options['/metos3d/data_path']
            except KeyError:
                pass
            else:
                options['/metos3d/data_dir'] = options['/metos3d/data_path']
                del options['/metos3d/data_path']
                print('/metos3d/data_path renamed to /metos3d/data_dir in job option file {}.'.format(options_file))

            try:
                options['/metos3d/data_path']
            except KeyError:
                pass
            else:
                options['/metos3d/data_dir'] = options['/metos3d/data_path']
                del options['/metos3d/data_path']
                print('/metos3d/data_path renamed to /metos3d/data_dir in job option file {}.'.format(options_file))

            try:
                options['/metos3d/tracer_output_path']
            except KeyError:
                pass
            else:
                options['/metos3d/tracer_output_dir'] = options['/metos3d/tracer_output_path']
                del options['/metos3d/tracer_output_path']
                print('/metos3d/tracer_output_path renamed to /metos3d/tracer_output_dir in job option file {}.'.format(options_file))

            try:
                options['/metos3d/output_path']
            except KeyError:
                pass
            else:
                options['/metos3d/output_dir'] = options['/metos3d/output_path']
                del options['/metos3d/output_path']
                print('/metos3d/output_path renamed to /metos3d/output_dir in job option file {}.'.format(options_file))

            try:
                options['/job/unfinished_file']
            except KeyError:
                options['/job/unfinished_file'] = os.path.join(job_options_dir, 'unfinished.txt')
                print('/job/unfinished_file added to job option file {}.'.format(options_file))
            
                
            

    update_job_options(update_function)



## general update functions for parameter files

def update_parameter_files(update_function):
    from simulation.model.constants import MODEL_NAMES, DATABASE_OUTPUT_DIR, DATABASE_MODEL_DIRNAME, DATABASE_PARAMETERS_FILENAME

    for model_name in MODEL_NAMES:
        model_dirname = DATABASE_MODEL_DIRNAME.format(model_name)
        model_dir = os.path.join(DATABASE_OUTPUT_DIR, model_dirname)
        
        for time_step_dir in util.io.fs.get_dirs(model_dir):
            parameter_set_dirs = util.io.fs.get_dirs(time_step_dir)
            logger.debug('{} parameter set dirs found in {}.'.format(len(parameter_set_dirs), time_step_dir))
    
            for parameter_set_dir in parameter_set_dirs:
                parameter_file = os.path.join(parameter_set_dir, DATABASE_PARAMETERS_FILENAME)

                util.io.fs.make_writable(parameter_file)
                p = np.loadtxt(parameter_file)
                p = update_function(p)
                np.savetxt(parameter_file, p)
                util.io.fs.make_read_only(parameter_file)


def update_parameter_files_add_total_concentration_factors():
    def update_function(p):
        assert len(p) in [7, 8]
        if len(p) == 7:
            p = np.concatenate([p, [1]])
        assert len(p) == 8
        return p
    update_parameter_files(update_function)



if __name__ == "__main__":
    with util.logging.Logger():
        # update_str_options('$NDOP_DIR/model_output', '${SIMULATION_OUTPUT_DIR}/model_dop_po4')
        # update_str_options('${NDOP_DIR}/model_output', '${SIMULATION_OUTPUT_DIR}/model_dop_po4')
        update_str_options('${MODEL_OUTPUT_DIR}/time_step_0001', '${SIMULATION_OUTPUT_DIR}/model_dop_po4/time_step_0001')
        update_new_option_entries()
        # update_parameter_files_add_total_concentration_factors()

    print('Update completed.')
