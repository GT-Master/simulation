import os
import stat
import tempfile
import warnings

import numpy as np

import simulation.constants
import simulation.model.data
import simulation.model.job
import simulation.model.constants

import measurements.land_sea_mask.data
import measurements.util.interpolate

import util.io.fs
import util.index_database.array_and_fs_based
import util.pattern
import util.math.interpolate
import util.batch.universal.system

import util.logging
logger = util.logging.logger



class Model():

    def __init__(self, model_options=None, job_setup=None):
        logger.debug('Model initiated with model_options {} and job setup {}.'.format(model_options, job_setup))
        
        ## init options
        self._model_options = {}
        if model_options is None:
            model_options = {}
        
        ## set model name
        try:
            name = model_options['model_name']
        except KeyError:
            name = simulation.model.constants.MODEL_NAMES[0]
        else:
            if not name in simulation.model.constants.MODEL_NAMES:
                raise ValueError('Model name {} is unknown. Only the names {} are supported.'.format(name, simulation.model.constants.MODEL_NAMES))
        self._model_options['model_name'] = name
        
        ## set total_concentration_factor_included_in_parameters
        try:
            total_concentration_factor_included_in_parameters = model_options['total_concentration_factor_included_in_parameters']
        except KeyError:
            total_concentration_factor_included_in_parameters = False
        self._model_options['total_concentration_factor_included_in_parameters'] = total_concentration_factor_included_in_parameters
        
        ## set parameter bounds and typical values
        self.parameters_lower_bound = simulation.model.constants.MODEL_PARAMETER_LOWER_BOUND[self.model_name]
        self.parameters_upper_bound = simulation.model.constants.MODEL_PARAMETER_UPPER_BOUND[self.model_name]
        self.parameters_typical_values = simulation.model.constants.MODEL_PARAMETER_TYPICAL[self.model_name]
        
        
        ## set spinup options
        def set_default_options(current_options, default_options):            
            if current_options is not None:
                for key, value in default_options.items():
                    try:
                        current_options[key]
                    except KeyError:
                        current_options[key] = value
                return current_options
            else:
                return default_options

        try:
            spinup_options = model_options['spinup_options']
        except KeyError:
            spinup_options = None
        spinup_options = set_default_options(spinup_options, simulation.model.constants.MODEL_DEFAULT_SPINUP_OPTIONS)
        if spinup_options['combination'] not in ['and', 'or']:
            raise ValueError('Combination "{}" unknown.'.format(spinup_options['combination']))
        self._model_options['spinup_options'] = spinup_options
        logger.debug('Using spinup options {}.'.format(self.spinup_options))
        
        
        ## set derivative options
        try:
            derivative_options = model_options['derivative_options']
        except KeyError:
            derivative_options = None
        derivative_options = set_default_options(derivative_options, simulation.model.constants.MODEL_DEFAULT_DERIVATIVE_OPTIONS)
        self._model_options['derivative_options'] = derivative_options
        logger.debug('Using derivative options {}.'.format(self.derivative_options))
        
        
        ## set time step
        try:
            time_step = model_options['time_step']
        except KeyError:
            time_step = 1
        else:
            if not time_step in simulation.model.constants.METOS_TIME_STEPS:
                raise ValueError('Wrong time_step in model options. Time step has to be in {} .'.format(time_step, simulation.model.constants.METOS_TIME_STEPS))
            assert simulation.model.constants.METOS_T_DIM % time_step == 0
        self._model_options['time_step'] = time_step
        
        
        ## set lsm
        time_dim = int(simulation.model.constants.METOS_T_DIM / time_step)
        self.lsm = measurements.land_sea_mask.data.LandSeaMaskTMM(t_dim=time_dim, t_centered=False)
        
        
        ## set tolerance options
        parameter_tolerance_options = {}
        try:
            model_options['parameter_tolerance_options']
        except KeyError:
            pass
        else:
            try:
                relative = model_options['parameter_tolerance_options']['relative']
            except KeyError:
                pass
            else:
                if len(relative) not in [1, self.parameter_len]:
                    raise ValueError('The relative tolerances must be a scalar or of equal length as the model parameters, but the relative tolerance is {} with length {} and the model parameters have length {}.'.format(relative, len(relative), self.parameter_len))
                if len(relative) > 1 and not self.total_concentration_factor_included_in_parameters:
                    relative = np.concatenate([relative, [0]])
                parameter_tolerance_options['relative'] = relative
            try:
                absolute = model_options['parameter_tolerance_options']['absolute']
            except KeyError:
                pass
            else:
                if len(absolute) not in [1, self.parameter_len]:
                    raise ValueError('The absolute tolerances must be a scalar or of equal length as the model parameters, but the absolute tolerance is {} with length {} and the model parameters have length {}.'.format(absolute, len(absolute), self.parameter_len))
                if len(absolute) > 1 and not self.total_concentration_factor_included_in_parameters:
                    absolute = np.concatenate([absolute, [10**-12]])
                parameter_tolerance_options['absolute'] = absolute
        self._model_options['parameter_tolerance_options'] = parameter_tolerance_options


        ## set job setup collection
        # convert job setup to job setup collection
        if job_setup is None:
            job_setup = {}

        job_setup_collection = {}
        keys = list(job_setup.keys())
        kinds = ['spinup', 'derivative', 'trajectory']
        if any(kind in keys for kind in kinds):
            job_setup_collection = job_setup
        else:
            job_setup_collection['spinup'] = job_setup

        # if not passed, use default job setups
        try:
            job_setup_collection['spinup']
        except KeyError:
            job_setup_collection['spinup'] = {}
        try:
            job_setup_collection['derivative']
        except KeyError:
            job_setup_collection['derivative'] = job_setup_collection['spinup'].copy()
            job_setup_collection['derivative']['nodes_setup'] = None
        try:
            job_setup_collection['trajectory']
        except KeyError:
            job_setup_collection['trajectory'] = job_setup_collection['derivative'].copy()
            job_setup_collection['trajectory']['nodes_setup'] = None

        # if no name passed, use default name
        try:
            default_name = job_setup['name']
        except KeyError:
            default_name = ''
        for kind in kinds:
            try:
                job_setup_collection[kind]['name']
            except KeyError:
                job_setup_collection[kind]['name'] = default_name

        self.job_setup_collection = job_setup_collection



        ## empty interpolator cache
        self._interpolator_cached = None
        
        ## database output dir
        self.database_output_dir = simulation.model.constants.DATABASE_OUTPUT_DIR
        
        ## init parameter db
        time_step_dir = self.time_step_dir()
        os.makedirs(time_step_dir, exist_ok=True)
        array_file = os.path.join(time_step_dir, simulation.model.constants.DATABASE_PARAMETERS_LOOKUP_ARRAY_FILENAME)
        value_file = os.path.join(time_step_dir, simulation.model.constants.DATABASE_PARAMETERS_SET_DIRNAME, simulation.model.constants.DATABASE_PARAMETERS_FILENAME)
        self._parameter_db = util.index_database.array_and_fs_based.Database(array_file, value_file, value_reliable_decimal_places=simulation.model.constants.DATABASE_PARAMETERS_RELIABLE_DECIMAL_PLACES, tolerance_options=self.parameter_tolerance_options)




    ## options
    
    @property
    def spinup_options(self):
        try:
            return self._model_options['spinup_options']
        except KeyError:
            return None
    
    @property
    def derivative_options(self):
        try:
            return self._model_options['derivative_options']
        except KeyError:
            return None
    
    @property
    def parameter_tolerance_options(self):
        try:
            return self._model_options['parameter_tolerance_options']
        except KeyError:
            return None
    
    @property
    def time_step(self):
        try:
            return self._model_options['time_step']
        except KeyError:
            return None
    
    @property
    def model_name(self):
        try:
            return self._model_options['model_name']
        except KeyError:
            return None
    
    
    @property
    def model_parameter_len(self):
        return len(self.parameters_lower_bound)

    @property
    def parameter_len(self):
        return self.model_parameter_len + self.total_concentration_factor_included_in_parameters
    
    @property
    def total_concentration_factor_included_in_parameters(self):
        try:
            return self._model_options['total_concentration_factor_included_in_parameters']
        except KeyError:
            return None
    
    @property
    def total_concentration_factor_included_in_database_entry(self):
        return True


    def job_setup(self, kind):
        job_setup = self.job_setup_collection[kind]
        job_setup = job_setup.copy()
        try:
            job_setup['nodes_setup'] = job_setup['nodes_setup'].copy()
        except KeyError:
            pass
        return job_setup

    
    ## check model parameters

    def check_parameters(self, parameters):
        parameters = np.asanyarray(parameters)
        
        parameters_dict = self.metos3d_model_parameters_dict(parameters)
        model_parameters = parameters_dict['model_parameters']

        ## check total concentration
        if self.total_concentration_factor_included_in_parameters:
            total_concentration_factor = parameters_dict['total_concentration_factor']
            if total_concentration_factor < 0:
                raise ValueError('The total concentration factor has to be greater or equal 0, but it is {}.'.format(total_concentration_factor))
        
        ## check length
        if len(model_parameters) != self.model_parameter_len:
            raise ValueError('The model parameters {} are not allowed. The length of the model_parameters have to be {} but it is {}.'.format(parameters, self.model_parameter_len, len(model_parameters)))
        
        ## check bounds
        if any(model_parameters < self.parameters_lower_bound):
            indices = np.where(model_parameters < self.parameters_lower_bound)
            raise ValueError('The model parameters {} are not allowed. The model_parameters with the indices {} are below their lower bound {}.'.format(model_parameters, indices, self.parameters_lower_bound[indices]))

        if any(model_parameters > self.parameters_upper_bound):
            indices = np.where(model_parameters > self.parameters_upper_bound)
            raise ValueError('The model parameters {} are not allowed. The model_parameters with the indices {} are above their upper bound {}.'.format(model_parameters, indices, self.parameters_upper_bound[indices]))
        
        return parameters
    
    
    def metos3d_model_parameters_dict(self, parameters):
        if self.total_concentration_factor_included_in_parameters:
            model_parameters = parameters[:-1]
            total_concentration_factor = parameters[-1]
        else:
            model_parameters = parameters
            total_concentration_factor = 1
        return {'model_parameters': model_parameters, 'total_concentration_factor': total_concentration_factor}            
    
    
    def database_parameter_entry(self, parameters):
        self.check_parameters(parameters)
        if self.total_concentration_factor_included_in_parameters:
            if self.total_concentration_factor_included_in_database_entry:
                return np.asanyarray(parameters)
            else:
                return np.asanyarray(parameters)[:-1]
        else:
            if self.total_concentration_factor_included_in_database_entry:
                return np.concatenate([parameters, [1,]])
            else:
                return np.asanyarray(parameters)
    
    
    def database_parameter_entry_to_metos3d_model_parameters_dict(self, database_parameter_entry):
        if self.total_concentration_factor_included_in_database_entry:
            model_parameters = database_parameter_entry[:-1]
            total_concentration_factor = database_parameter_entry[-1]
        else:
            model_parameters = database_parameter_entry
            total_concentration_factor = 1
        return {'model_parameters': model_parameters, 'total_concentration_factor': total_concentration_factor}   
    
    



    ## access to dirs

    def model_dir(self):
        model_dirname = simulation.model.constants.DATABASE_MODEL_DIRNAME.format(self.model_name)
        model_dir = os.path.join(self.database_output_dir, model_dirname)
        logger.debug('Returning model directory {} for model {}.'.format(model_dir, self.model_name))
        return model_dir
    

    def time_step_dir(self):
        time_step_dirname = simulation.model.constants.DATABASE_TIME_STEP_DIRNAME.format(self.time_step)
        time_step_dir = os.path.join(self.model_dir(), time_step_dirname, '')
        logger.debug('Returning time step directory {} for time step {}.'.format(time_step_dir, self.time_step))
        return time_step_dir


    def parameter_set_dir_with_index(self, index):
        if index is not None:
            dir = os.path.join(self.time_step_dir(), simulation.model.constants.DATABASE_PARAMETERS_SET_DIRNAME.format(index))
            logger.debug('Returning parameter set directory {} for index {}.'.format(dir, index))
            return dir
        else:
            return None
    
    
    def closest_parameter_set_dir(self, parameters, no_spinup_okay=True):
        logger.debug('Searching for directory for parameters as close as possible to {} with no_spinup_okay {}.'.format(parameters, no_spinup_okay))
        database_parameter_entry = self.database_parameter_entry(parameters)
        
        if no_spinup_okay:            
            ## get closest index
            closest_index = self._parameter_db.closest_index(database_parameter_entry)
        else:
            ## get closest indices
            closest_indices = self._parameter_db.closest_indices(database_parameter_entry)
            
            ## check if run dirs exist
            i = 0
            while i < len(closest_indices) and self.last_run_dir(self.spinup_dir_with_index[i]) is None:
                i = i +1
            if i < len(closest_indices):
                closest_index = closest_indices[i]
            else:
                closest_index = None
        
        ## get parameter set dir and return 
        closest_parameter_set_dir = self.parameter_set_dir_with_index(closest_index)
        logger.debug('Closest parameter set dir is {}.'.format(closest_parameter_set_dir))
        return closest_parameter_set_dir


    def parameter_set_dir(self, parameters, create=True):
        ## search for directories with matching parameters
        logger.debug('Searching parameter directory for parameters {} with create {}.'.format(parameters, create))
        database_parameter_entry = self.database_parameter_entry(parameters)
        
        index = self._parameter_db.index(database_parameter_entry)
        if index is None and create:
            index = self._parameter_db.add_value(database_parameter_entry)
        parameter_set_dir = self.parameter_set_dir_with_index(index)
        
        ## return
        logger.debug('Matching directory for parameters found at {}.'.format(parameter_set_dir))
        assert parameter_set_dir is not None or not create
        return parameter_set_dir


    def spinup_dir_with_index(self, index):
        if index is not None:
            dir = os.path.join(self.parameter_set_dir_with_index(index), simulation.model.constants.DATABASE_SPINUP_DIRNAME)
            logger.debug('Returning spinup directory {} for index {}.'.format(dir, index))
            return dir
        else:
            return None


    def spinup_dir(self, parameters_or_parameter_set_dir, create=True):
        ## get parameter set dir
        if isinstance(parameters_or_parameter_set_dir, str):
            parameter_set_dir = parameters_or_parameter_set_dir
        else:
            parameters = np.asanyarray(parameters_or_parameter_set_dir)
            parameter_set_dir = self.parameter_set_dir(parameters, create=create)
        
        ## return
        spinup_dir = os.path.join(parameter_set_dir, simulation.model.constants.DATABASE_SPINUP_DIRNAME)
        logger.debug('Returning spinup directory {} for parameter set dir {}.'.format(spinup_dir, parameter_set_dir))
        return spinup_dir
    

    ## access to run dirs

    def run_dirs(self, search_path):
        from .constants import DATABASE_RUN_DIRNAME

        run_dir_condition = lambda file: os.path.isdir(file) and util.pattern.is_matching(os.path.basename(file), DATABASE_RUN_DIRNAME)
        try:
            run_dirs = util.io.fs.filter_files(search_path, run_dir_condition)
        except (OSError, IOError) as exception:
            warnings.warn('It could not been searched in the search path "' + search_path + '": ' + str(exception))
            run_dirs = []

        return run_dirs


    def last_run_dir(self, search_path):
        logger.debug('Searching for last run in {}.'.format(search_path))

        last_run_index =  len(self.run_dirs(search_path)) - 1
        
        if last_run_index >= 0:
            last_run_dirname = simulation.model.constants.DATABASE_RUN_DIRNAME.format(last_run_index)
            last_run_dir = os.path.join(search_path, last_run_dirname)
            
            ## check job options file
            with simulation.model.job.Metos3D_Job(last_run_dir, force_load=True) as job:
                pass
        else:
            last_run_dir = None

        logger.debug('Returning last run directory {}.'.format(last_run_dir))
        return last_run_dir


    def previous_run_dir(self, run_dir):
        from .constants import DATABASE_RUN_DIRNAME

        (spinup_dir, run_dirname) = os.path.split(run_dir)
        run_index = util.pattern.get_int_in_string(run_dirname)
        if run_index > 0:
            previous_run_dirname = DATABASE_RUN_DIRNAME.format(run_index - 1)
            previous_run_dir = os.path.join(spinup_dir, previous_run_dirname)
        else:
            previous_run_dir = None

        return previous_run_dir
    
    
    def make_new_run_dir(self, output_path):
        ## get next run index
        os.makedirs(output_path, exist_ok=True)
        next_run_index = len(self.run_dirs(output_path))

        ## create run dir
        run_dirname = simulation.model.constants.DATABASE_RUN_DIRNAME.format(next_run_index)
        run_dir = os.path.join(output_path, run_dirname)

        logger.debug('Creating new run directory {} at {}.'.format(run_dir, output_path))
        os.makedirs(run_dir, exist_ok=False)
        return run_dir
    
    
    
    
    def matching_run_dir(self, parameters_or_parameter_set_dir, spinup_options, start_from_closest_parameters=False):
        from .constants import DATABASE_SPINUP_DIRNAME, DATABASE_PARAMETERS_FILENAME, MODEL_SPINUP_MAX_YEARS
        
        ## get spinup dir
        spinup_dir = self.spinup_dir(parameters_or_parameter_set_dir)
        logger.debug('Searching for matching spinup run with options {} in {}.'.format(spinup_options, spinup_dir))
        
        ## get parameter set dir
        parameter_set_dir = os.path.dirname(spinup_dir)

        ## get last run dir
        last_run_dir = self.last_run_dir(spinup_dir)

        ## matching run found
        if self.is_run_matching_options(last_run_dir, spinup_options):
            run_dir = last_run_dir
            logger.debug('Matching spinup run found at {}.'.format(last_run_dir))

        ## create new run
        else:
            logger.debug('No matching spinup run found.')

            ## get parameters
            parameter_file = os.path.join(parameter_set_dir, DATABASE_PARAMETERS_FILENAME)
            parameters = np.loadtxt(parameter_file)

            ## no previous run exists and starting from closest parameters get last run from closest parameters
            if last_run_dir is None and start_from_closest_parameters:
                closest_parameter_set_dir = self.closest_parameter_set_dir(parameters, no_spinup_okay=False)
                closest_spinup_dir = os.path.join(closest_parameter_set_dir, DATABASE_SPINUP_DIRNAME)
                last_run_dir = self.last_run_dir(closest_spinup_dir)

            ## finish last run
            if last_run_dir is not None:
                self.wait_until_run_job_finished(last_run_dir)

            ## make new run
            years = spinup_options['years']
            tolerance = spinup_options['tolerance']
            combination = spinup_options['combination']

            if combination == 'or':
                ## create new run
                run_dir = self.make_new_run_dir(spinup_dir)
                
                ## get metos3d model parameters
                if last_run_dir is None:
                    total_concentration_factor = parameters[-1]
                else:
                    total_concentration_factor = 1
                model_parameters = parameters[:-1]
                
                ## calculate last years
                if last_run_dir is not None:
                    last_years = self.get_total_years(last_run_dir)
                    logger.debug('Found previous run(s) with total {} years.'.format(last_years))
                else:
                    last_years = 0
                
                ## start new run
                self.start_run(model_parameters, run_dir, years-last_years, tolerance=tolerance, job_setup=self.job_setup('spinup'), total_concentration_factor=total_concentration_factor, tracer_input_dir=last_run_dir, wait_until_finished=True)
                
            elif combination == 'and':
                run_dir = self.matching_run_dir(parameter_set_dir, {'years':years, 'tolerance':0, 'combination':'or'}, start_from_closest_parameters)
                run_dir = self.matching_run_dir(parameter_set_dir, {'years':MODEL_SPINUP_MAX_YEARS, 'tolerance':tolerance, 'combination':'or'}, start_from_closest_parameters)

            logger.debug('Spinup run directory created at {}.'.format(run_dir))

        return run_dir
    
        



    ## run job

    def start_run(self, model_parameters, output_path, years, tolerance=0, job_setup=None, write_trajectory=False, total_concentration_factor=1, tracer_input_dir=None, make_read_only=True, wait_until_finished=True):
        logger.debug('Running job with years {}, tolerance {}, total_concentration_factor {} and tracer_input_dir {}.'.format(years, tolerance, total_concentration_factor, tracer_input_dir))

        ## execute job
        output_path_with_env = output_path.replace(simulation.constants.SIMULATION_OUTPUT_DIR, '${{{}}}'.format(simulation.constants.SIMULATION_OUTPUT_DIR_ENV_NAME))
        with simulation.model.job.Metos3D_Job(output_path_with_env) as job:
            job.write_job_file(self.model_name, model_parameters, years=years, tolerance=tolerance, time_step=self.time_step, total_concentration_factor=total_concentration_factor, write_trajectory=write_trajectory, tracer_input_dir=tracer_input_dir, job_setup=job_setup)
            job.start()
            job.make_read_only_input(make_read_only)

        ## wait to finish
        if wait_until_finished:
            self.wait_until_run_job_finished(output_path, make_read_only=make_read_only)
        else:
            logger.debug('Not waiting for job to finish.')


    def wait_until_run_job_finished(self, run_dir, make_read_only=True):
        with simulation.model.job.Metos3D_Job(run_dir, force_load=True) as job:
            job.make_read_only_input(make_read_only)
            job.wait_until_finished()
            job.make_read_only_output(make_read_only)




    ##  access run properties
    
    def is_run_matching_options(self, run_dir, spinup_options=None):
        from .constants import MODEL_SPINUP_MAX_YEARS

        years = spinup_options['years']
        tolerance = spinup_options['tolerance']
        combination = spinup_options['combination']

        if run_dir is not None:
            run_years = self.get_total_years(run_dir)
            run_tolerance = self.get_real_tolerance(run_dir)

            if combination == 'and':
                is_matching = (run_years >= years and run_tolerance <= tolerance) or run_years >= MODEL_SPINUP_MAX_YEARS
                if is_matching and run_tolerance > tolerance:
                    warnings.warn('The run {} does not match the desired tolerance {}, but the max spinup years {} are reached.'.format(run_dir, tolerance, MODEL_SPINUP_MAX_YEARS))
            elif combination == 'or':
                is_matching = (run_years >= years or run_tolerance <= tolerance)
            else:
                raise ValueError('Combination "{}" unknown.'.format(combination))
                
            if is_matching:
                logger.debug('Run in {} with years {} and tolerance {} is matching spinup options {}.'.format(run_dir, run_years, run_tolerance, spinup_options))
            else:
                logger.debug('Run in {} with years {} and tolerance {} is not matching spinup options {}.'.format(run_dir, run_years, run_tolerance, spinup_options))
        else:
            is_matching = False
            logger.debug('Run in {} is not matching spinup options {}. No run available.'.format(run_dir, spinup_options))


        return is_matching


    def get_total_years(self, run_dir):
        total_years = 0

        while run_dir is not None:
            with simulation.model.job.Metos3D_Job(run_dir, force_load=True) as job:
                years = job.last_year
            total_years += years
            run_dir = self.previous_run_dir(run_dir)

        return total_years



    def get_real_tolerance(self, run_dir):
        with simulation.model.job.Metos3D_Job(run_dir, force_load=True) as job:
            tolerance = job.last_tolerance

        return tolerance



    def get_time_step(self, run_dir):
        with simulation.model.job.Metos3D_Job(run_dir, force_load=True) as job:
            time_step = job.time_step

        return time_step





    ## access to model values (auxiliary)

    def _get_trajectory(self, load_trajectory_function, run_dir, model_parameters):
        from .constants import METOS_TRACER_DIM
        from util.constants import TMP_DIR

        assert callable(load_trajectory_function)

        trajectory_values = ()

        ## create trajectory
        if TMP_DIR is not None:
            tmp_dir = TMP_DIR
            os.makedirs(tmp_dir, exist_ok=True)
        else:
            tmp_dir = run_dir

        ## write trajectory
        trajectory_dir = tempfile.mkdtemp(dir=tmp_dir, prefix='trajectory_tmp_')
        self.start_run(model_parameters, trajectory_dir, years=1, tolerance=0, job_setup=self.job_setup('trajectory'), tracer_input_dir=run_dir, write_trajectory=True, make_read_only=False)

        ## read trajectory
        trajectory_output_dir = os.path.join(trajectory_dir, 'trajectory')
        for tracer_index in range(METOS_TRACER_DIM):
            tracer_trajectory_values = load_trajectory_function(trajectory_output_dir, tracer_index)
            trajectory_values += (tracer_trajectory_values,)

        ## remove trajectory
        util.io.fs.remove_recursively(trajectory_dir, not_exist_okay=True, exclude_dir=False)

        ## return
        assert len(trajectory_values) == METOS_TRACER_DIM
        return trajectory_values


    def _get_load_trajectory_function_for_all(self, time_dim_desired):
        load_trajectory_function = lambda trajectory_path, tracer_index : simulation.model.data.load_trajectories_to_map(trajectory_path, tracer_index, time_dim_desired=time_dim_desired)
        return load_trajectory_function


    def _get_load_trajectory_function_for_points(self, points):
        from .constants import MODEL_INTERPOLATOR_NUMBER_OF_LINEAR_INTERPOLATOR

        ## convert to map indices
        interpolation_points = []
        for tracer_points in points:
            tracer_interpolation_points = np.array(tracer_points, copy=True)
            tracer_interpolation_points = self.lsm.coordinates_to_map_indices(tracer_interpolation_points)
            assert tracer_interpolation_points.ndim == 2 and tracer_interpolation_points.shape[1] == 4
            
            if MODEL_INTERPOLATOR_NUMBER_OF_LINEAR_INTERPOLATOR > 0:
                for value_min, index in ([np.where(self.lsm.lsm > 0)[1].min(), 2], [0, 3]):
                    for k in range(len(tracer_interpolation_points)):
                        if tracer_interpolation_points[k, index] < value_min:
                            tracer_interpolation_points[k, index] = value_min
                for value_max, index in ([np.where(self.lsm.lsm > 0)[1].max(), 2], [self.lsm.z_dim - 1, 3]):
                    for k in range(len(tracer_interpolation_points)):
                        if tracer_interpolation_points[k, index] > value_max:
                            tracer_interpolation_points[k, index] = value_max
            
            interpolation_points.append(tracer_interpolation_points)

        ## load function
        def load_trajectory_function(trajectory_path, tracer_index):
            tracer_trajectory = simulation.model.data.load_trajectories_to_map_index_array(trajectory_path, tracer_index=tracer_index)
            interpolated_values_for_tracer = self._interpolate(tracer_trajectory, interpolation_points[tracer_index])
            return interpolated_values_for_tracer
            

        return load_trajectory_function



    def _interpolate(self, data, interpolation_points, use_cache=False):
        from .constants import MODEL_INTERPOLATOR_FILE, MODEL_INTERPOLATOR_AMOUNT_OF_WRAP_AROUND, MODEL_INTERPOLATOR_NUMBER_OF_LINEAR_INTERPOLATOR, MODEL_INTERPOLATOR_TOTAL_OVERLAPPING_OF_LINEAR_INTERPOLATOR, METOS_DIM

        data_points = data[:,:-1]
        data_values = data[:,-1]
        interpolator_file = MODEL_INTERPOLATOR_FILE

        ## try to get cached interpolator
        interpolator = self._interpolator_cached
        if interpolator is not None:
            interpolator.data_values = data_values
            logger.debug('Returning cached interpolator.')
        else:
            ## otherwise try to get saved interpolator
            if use_cache and os.path.exists(interpolator_file):
                interpolator = util.math.interpolate.Interpolator_Base.load(interpolator_file)
                interpolator.data_values = data_values
                logger.debug('Returning interpolator loaded from {}.'.format(interpolator_file))
            ## if no interpolator exists, create new interpolator
            else:
                interpolator = util.math.interpolate.Periodic_Interpolator(data_points=data_points, data_values=data_values, point_range_size=METOS_DIM, scaling_values=(METOS_DIM[1]/METOS_DIM[0], None, None, None), wrap_around_amount=MODEL_INTERPOLATOR_AMOUNT_OF_WRAP_AROUND, number_of_linear_interpolators=MODEL_INTERPOLATOR_NUMBER_OF_LINEAR_INTERPOLATOR, total_overlapping_linear_interpolators=MODEL_INTERPOLATOR_TOTAL_OVERLAPPING_OF_LINEAR_INTERPOLATOR)
                logger.debug('Returning new created interpolator.')

            self._interpolator_cached = interpolator

        ## interpolate
        interpolated_values = interpolator.interpolate(interpolation_points)

        ## save interpolate if cache used
        if use_cache and not os.path.exists(interpolator_file):
            interpolator.save(interpolator_file)

        ## return interpolated values
        assert not np.any(np.isnan(interpolated_values))
#         assert np.all(interpolator.data_points == data_points)
#         assert np.all(interpolator.data_values == data_values)

        return interpolated_values



    def _f(self, load_trajectory_function, parameters, spinup_options=None):
        from .constants import MODEL_START_FROM_CLOSEST_PARAMETER_SET
        
        matching_run_dir = self.matching_run_dir(parameters, spinup_options, start_from_closest_parameters=MODEL_START_FROM_CLOSEST_PARAMETER_SET)
        model_parameters = self.metos3d_model_parameters_dict(parameters)['model_parameters']
        f = self._get_trajectory(load_trajectory_function, matching_run_dir, model_parameters)

        assert f is not None
        return f



    def _df(self, load_trajectory_function, parameters, spinup_options=None, partial_derivatives_mask=None):
        from .constants import DATABASE_DERIVATIVE_DIRNAME, DATABASE_PARTIAL_DERIVATIVE_DIRNAME, METOS_TRACER_DIM, MODEL_START_FROM_CLOSEST_PARAMETER_SET

        MODEL_DERIVATIVE_SPINUP_YEARS = self.derivative_options['years']
        MODEL_DERIVATIVE_STEP_SIZE = self.derivative_options['step_size']
        MODEL_DERIVATIVE_ACCURACY_ORDER = self.derivative_options['accuracy_order']

        parameters = np.asanyarray(parameters)
        
        ## prepare partial_derivatives_mask
        if partial_derivatives_mask is None:
            partial_derivatives_mask = np.ones(len(parameters), dtype=np.bool)
        else:
            if len(partial_derivatives_mask) != len(parameters):
                raise ValueError('Partial derivatives mask must have same length as the parameters, but its length is {} and the length of the parameters is {}.'.format(len(partial_derivatives_mask), len(parameters)))
            partial_derivatives_mask = np.asanyarray(partial_derivatives_mask, dtype=np.bool)
        if not self.total_concentration_factor_included_in_parameters:
            partial_derivatives_mask = np.concatenate([partial_derivatives_mask, [False,]])
        
        logger.debug('Calculating df values for parameters {} using partial_derivatives_mask {}.'.format(parameters, partial_derivatives_mask))
        
        
        ## chose h factors
        if MODEL_DERIVATIVE_ACCURACY_ORDER == 1:
            h_factors = (1,)
        elif MODEL_DERIVATIVE_ACCURACY_ORDER == 2:
            h_factors = (1, -1)
        else:
            raise ValueError('Accuracy order {} not supported.'.format(MODEL_DERIVATIVE_ACCURACY_ORDER))

        ## search directories
        parameter_set_dir = self.parameter_set_dir(parameters, create=True)
        derivative_dir = os.path.join(parameter_set_dir, DATABASE_DERIVATIVE_DIRNAME.format(MODEL_DERIVATIVE_STEP_SIZE))

        ## get spinup run
        years = spinup_options['years']
        tolerance = spinup_options['tolerance']
        combination = spinup_options['combination']
        spinup_options_derivative_base = {'years':years - MODEL_DERIVATIVE_SPINUP_YEARS, 'tolerance':tolerance, 'combination':combination}
        spinup_matching_run_dir = self.matching_run_dir(parameter_set_dir, spinup_options_derivative_base, start_from_closest_parameters=MODEL_START_FROM_CLOSEST_PARAMETER_SET)
        spinup_matching_run_years = self.get_total_years(spinup_matching_run_dir)

        ## get f if accuracy_order is 1
        if MODEL_DERIVATIVE_ACCURACY_ORDER == 1:
            spinup_previous_run_dir = self.previous_run_dir(spinup_matching_run_dir)
            spinup_previous_run_years = self.get_total_years(spinup_previous_run_dir)
            if spinup_previous_run_years == spinup_matching_run_years - MODEL_DERIVATIVE_SPINUP_YEARS:
                spinup_matching_run_dir = spinup_previous_run_dir
                spinup_matching_run_years = spinup_previous_run_years

            f = self._f(load_trajectory_function, parameters, {'years':spinup_matching_run_years + MODEL_DERIVATIVE_SPINUP_YEARS, 'tolerance':0, 'combination':'or'})
            
        ## init values
        database_parameter_entry = self.database_parameter_entry(parameters)
        database_parameter_entry[-1] = 1
        parameters_len = len(database_parameter_entry)
        parameters_lower_bound = np.concatenate([self.parameters_lower_bound, [0,]])
        parameters_upper_bound = np.concatenate([self.parameters_upper_bound, [float('inf'),]])
        parameters_typical_values =  np.concatenate([self.parameters_typical_values, [1,]])

        h_factors_len = len(h_factors)
        h = np.empty((parameters_len, h_factors_len))
        
        parameters_for_derivative = np.empty((parameters_len, h_factors_len, parameters_len))

        job_setup = self.job_setup('derivative')
        partial_derivative_run_dirs = np.empty([parameters_len, h_factors_len], dtype=object)

        ## start partial derivative runs
        for parameter_index in range(parameters_len):
            if partial_derivatives_mask[parameter_index]:
                
                h_i = parameters_typical_values[parameter_index] * MODEL_DERIVATIVE_STEP_SIZE
    
                for h_factor_index in range(h_factors_len):
    
                    ## prepare parameters for derivative
                    parameters_for_derivative[parameter_index, h_factor_index] = np.copy(database_parameter_entry)
                    h[parameter_index, h_factor_index] = h_factors[h_factor_index] * h_i
                    parameters_for_derivative[parameter_index, h_factor_index, parameter_index] += h[parameter_index, h_factor_index]
    
                    ## consider bounds
                    violates_lower_bound = parameters_for_derivative[parameter_index, h_factor_index, parameter_index] < parameters_lower_bound[parameter_index]
                    violates_upper_bound = parameters_for_derivative[parameter_index, h_factor_index, parameter_index] > parameters_upper_bound[parameter_index]
    
                    if MODEL_DERIVATIVE_ACCURACY_ORDER == 1:
                        if violates_lower_bound or violates_upper_bound:
                            h[parameter_index, h_factor_index] *= -1
                            parameters_for_derivative[parameter_index, h_factor_index, parameter_index] = parameters[parameter_index] + h[parameter_index, h_factor_index]
                    else:
                        if violates_lower_bound:
                            parameters_for_derivative[parameter_index, h_factor_index, parameter_index] = parameters_lower_bound[parameter_index]
                        elif violates_upper_bound:
                            parameters_for_derivative[parameter_index, h_factor_index, parameter_index] = parameters_upper_bound[parameter_index]
    
                    ## calculate h   (improvement of accuracy of h)
                    h[parameter_index, h_factor_index] = parameters_for_derivative[parameter_index, h_factor_index, parameter_index] - parameters[parameter_index]
    
                    logger.debug('Calculating finite differences approximation for parameter index {} with h value {}.'.format(parameter_index, h[parameter_index, h_factor_index]))
    
                    ## get run dir
                    h_factor = int(np.sign(h[parameter_index, h_factor_index]))
                    partial_derivative_dirname = DATABASE_PARTIAL_DERIVATIVE_DIRNAME.format(parameter_index, h_factor)
                    partial_derivative_dir = os.path.join(derivative_dir, partial_derivative_dirname)
                    try:
                        partial_derivative_run_dir = self.last_run_dir(partial_derivative_dir)
                    except OSError:
                        partial_derivative_run_dir = None
                    
                    ## get corresponding spinup run dir
                    if partial_derivative_run_dir is not None:
                        try:
                            with simulation.model.job.Metos3D_Job(partial_derivative_run_dir, force_load=True) as job:
                                partial_derivative_spinup_run_dir = job.tracer_input_dir
                        except OSError:
                            partial_derivative_spinup_run_dir = None
    
                    ## make new run if run not matching
                    if not self.is_run_matching_options(partial_derivative_run_dir, {'years':MODEL_DERIVATIVE_SPINUP_YEARS, 'tolerance':0, 'combination':'or'}) or not self.is_run_matching_options(partial_derivative_spinup_run_dir, spinup_options_derivative_base):   
                        
                        ## remove old run
                        if partial_derivative_run_dir is not None:
                            logger.debug('Old partial derivative spinup run {} is not matching desired option. It is removed.'.format(partial_derivative_run_dir))
                            util.io.fs.remove_recursively(partial_derivative_run_dir, not_exist_okay=True, exclude_dir=True)
                        
                        ## create new run dir
                        partial_derivative_run_dir = self.make_new_run_dir(partial_derivative_dir)   
                        
                        ## if no job setup available, get best job setup
                        if job_setup['nodes_setup'] is None:
                            job_setup['nodes_setup'] = util.batch.universal.system.NodeSetup()
    
                        ## start job
                        metos3d_model_parameters_dict = self.database_parameter_entry_to_metos3d_model_parameters_dict(parameters_for_derivative[parameter_index, h_factor_index])
                        model_parameters = metos3d_model_parameters_dict['model_parameters']
                        del metos3d_model_parameters_dict['model_parameters']
                        self.start_run(model_parameters, partial_derivative_run_dir, MODEL_DERIVATIVE_SPINUP_YEARS, tolerance=0, **metos3d_model_parameters_dict, job_setup=job_setup, tracer_input_dir=spinup_matching_run_dir, wait_until_finished=False)
                        
                    partial_derivative_run_dirs[parameter_index, h_factor_index] = partial_derivative_run_dir


        ## make trajectories and calculate df
        df = [None] * METOS_TRACER_DIM

        for parameter_index in range(parameters_len):
            if partial_derivatives_mask[parameter_index]:
                
                ## include result for each h factor
                for h_factor_index in range(h_factors_len):
                    ## wait partial derivative run to finish
                    partial_derivative_run_dir = partial_derivative_run_dirs[parameter_index, h_factor_index]
                    self.wait_until_run_job_finished(partial_derivative_run_dir)
    
                    ## get trajectory
                    model_parameters = self.database_parameter_entry_to_metos3d_model_parameters_dict(parameters_for_derivative[parameter_index, h_factor_index])['model_parameters']
                    trajectory = self._get_trajectory(load_trajectory_function, partial_derivative_run_dir, model_parameters)
                    
                    ## add to df
                    for tracer_index in range(METOS_TRACER_DIM):
                        if df[tracer_index] is None:
                            df[tracer_index] = np.zeros((parameters_len,) + trajectory[tracer_index].shape)
                        df[tracer_index][parameter_index] += (-1)**h_factor_index * trajectory[tracer_index]
    
                ## calculate df
                for tracer_index in range(METOS_TRACER_DIM):
                    if MODEL_DERIVATIVE_ACCURACY_ORDER == 1:
                        df[tracer_index][parameter_index] -= f[tracer_index]
                        df[tracer_index][parameter_index] /= h[parameter_index]
                    else:
                        df[tracer_index][parameter_index] /= np.sum(np.abs(h[parameter_index]))
        
        ## apply partial_derivatives_mask
        for tracer_index in range(METOS_TRACER_DIM):
            df[tracer_index] = df[tracer_index][partial_derivatives_mask]
            assert len(df[tracer_index]) == partial_derivatives_mask.sum()

        assert len(df) == METOS_TRACER_DIM
        return df


    ## access to model values

    def f_boxes(self, parameters, time_dim_desired):
        logger.debug('Calculating all f values for parameters {} with time dimension {}.'.format(parameters, time_dim_desired))
        
        ## check input
        self.check_parameters(parameters)
        
        ## calculate f
        f = self._f(self._get_load_trajectory_function_for_all(time_dim_desired), parameters, self.spinup_options)
        
        assert len(f) == 2
        return f


    def f_points(self, parameters, points):
        logger.debug('Calculating f values for parameters {} at {} points.'.format(parameters, tuple(map(len, points))))
        
        ## check input
        if len(points) != 2:
            raise ValueError('Points have to be a sequence of 2 point arrays. But its length is {}.'.format(len(points)))
        self.check_parameters(parameters)
        
        ## calculate f
        f = self._f(self._get_load_trajectory_function_for_points(points), parameters, self.spinup_options)

        assert len(f) == 2
        assert (not np.any(np.isnan(f[0]))) and (not np.any(np.isnan(f[1])))
        return f
    

    def df_boxes(self, parameters, time_dim_desired, partial_derivatives_mask=None):
        logger.debug('Calculating all df values for parameters {} with time dimension {}.'.format(parameters, time_dim_desired))
        
        ## check input
        self.check_parameters(parameters)
        
        ## calculate df
        df = self._df(self._get_load_trajectory_function_for_all(time_dim_desired=time_dim_desired), parameters, self.spinup_options, partial_derivatives_mask=partial_derivatives_mask)
        
        assert len(df) == 2
        # assert df.shape[-1] == len(parameters)
        return df


    def df_points(self, parameters, points, partial_derivatives_mask=None):
        logger.debug('Calculating df values for parameters {} at {} points.'.format(parameters, tuple(map(len, points))))
        
        ## check input
        if len(points) != 2:
            raise ValueError('Points have to be a sequence of 2 point arrays. But its length is {}.'.format(len(points)))
        self.check_parameters(parameters)
        
        ## calculate df
        df = self._df(self._get_load_trajectory_function_for_points(points), parameters, self.spinup_options, partial_derivatives_mask=partial_derivatives_mask)
        
        assert len(df) == 2
        # assert df.shape[-1] == len(parameters)
        assert (not np.any(np.isnan(df[0]))) and (not np.any(np.isnan(df[1])))
        return df


