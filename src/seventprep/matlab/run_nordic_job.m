function run_nordic_job(job_json)
%RUN_NORDIC_JOB Execute magnitude-only NIFTI_NORDIC from a JSON job file.
%
% NORDIC itself is intentionally not distributed with seventprep. The job
% must point at an authorized local checkout containing NIFTI_NORDIC.m.

job = jsondecode(fileread(job_json));
if ~isfolder(job.nordic_path)
    error('NORDIC checkout does not exist: %s', job.nordic_path);
end
if ~isfile(fullfile(job.nordic_path, 'NIFTI_NORDIC.m'))
    error('NIFTI_NORDIC.m was not found in: %s', job.nordic_path);
end
if ~isfile(job.magnitude_file)
    error('Magnitude input does not exist: %s', job.magnitude_file);
end
if ~isfolder(job.output_directory)
    mkdir(job.output_directory);
end

addpath(genpath(job.nordic_path));

ARG = struct();
ARG.DIROUT = [job.output_directory filesep];
ARG.magnitude_only = 1;
ARG.noise_volume_last = job.noise_volume_last;
ARG.factor_error = job.factor_error;
ARG.save_gfactor_map = double(job.save_gfactor_map);
ARG.save_add_info = double(job.save_additional_info);
ARG.write_gzipped_niftis = 1;

% The second input is ignored when magnitude_only == 1. Passing the same
% file avoids fabricating a phase image and follows NIFTI_NORDIC's API.
NIFTI_NORDIC(job.magnitude_file, job.magnitude_file, job.output_prefix, ARG);
end
