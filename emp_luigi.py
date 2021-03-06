import luigi
import inspect, os
import pandas as pd
import time
from db import extract
from db import log
from db import sql
from forecast import compute
from forecast import util
import shutil
import luigi.contrib.hadoop
from pathlib import Path
from sqlalchemy import create_engine
from pysandag.database import get_connection_string
import defm_luigi as dl
import numpy as np
from pysandag import database


class EmpPopulation(luigi.Task):

    def requires(self):
        return None

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):

        my_file = Path('temp/data.h5')
        if my_file.is_file():
            print'File exists'
        else:
            db_run_id = log.new_run(name='emp_run_log')
            run_id = pd.Series([db_run_id])
            run_id.to_hdf('temp/data.h5', 'run_id',  mode='a')
            engine = create_engine(get_connection_string("model_config.yml", 'output_database'))
            db_connection_string = database.get_connection_string('model_config.yml', 'in_db')
            sql_in_engine = create_engine(db_connection_string)

            rate_versions = util.yaml_to_dict('model_config.yml', 'rate_versions')
            tables = util.yaml_to_dict('model_config.yml', 'db_tables')
            in_query = getattr(sql, 'inc_pop') % (tables['inc_pop_table'], rate_versions['inc_pop'])
            in_query2 = getattr(sql, 'inc_pop_mil') % (tables['population_table'], rate_versions['population'])

            pop = pd.read_sql(in_query, engine, index_col=['age', 'race_ethn', 'sex', 'mildep'])
            pop_mil = pd.read_sql(in_query2, sql_in_engine, index_col=['age', 'race_ethn', 'sex', 'mildep'])

            pop = pop.join(pop_mil)
            pop['persons'] = (pop['persons'] - pop['mil_mildep'])
            pop = pop.reset_index(drop=False)

            pop['age_cat'] = ''
            pop.loc[pop['age'].isin(list(range(0, 5))), ['age_cat']] = '00_04'
            pop.loc[pop['age'].isin(list(range(5, 10))), ['age_cat']] = '05_09'
            pop.loc[pop['age'].isin(list(range(10, 15))), ['age_cat']] = '10_14'
            pop.loc[pop['age'].isin(list(range(15, 18))), ['age_cat']] = '15_17'
            pop.loc[pop['age'].isin(list(range(18, 20))), ['age_cat']] = '18_19'
            pop.loc[pop['age'].isin(list(range(20, 21))), ['age_cat']] = '20_20'
            pop.loc[pop['age'].isin(list(range(21, 22))), ['age_cat']] = '21_21'
            pop.loc[pop['age'].isin(list(range(22, 25))), ['age_cat']] = '22_24'
            pop.loc[pop['age'].isin(list(range(25, 30))), ['age_cat']] = '25_29'
            pop.loc[pop['age'].isin(list(range(30, 35))), ['age_cat']] = '30_34'
            pop.loc[pop['age'].isin(list(range(35, 40))), ['age_cat']] = '35_39'
            pop.loc[pop['age'].isin(list(range(40, 45))), ['age_cat']] = '40_44'
            pop.loc[pop['age'].isin(list(range(45, 50))), ['age_cat']] = '45_49'
            pop.loc[pop['age'].isin(list(range(50, 55))), ['age_cat']] = '50_54'
            pop.loc[pop['age'].isin(list(range(55, 60))), ['age_cat']] = '55_59'
            pop.loc[pop['age'].isin(list(range(60, 62))), ['age_cat']] = '60_61'
            pop.loc[pop['age'].isin(list(range(62, 65))), ['age_cat']] = '62_64'
            pop.loc[pop['age'].isin(list(range(65, 67))), ['age_cat']] = '65_66'
            pop.loc[pop['age'].isin(list(range(67, 70))), ['age_cat']] = '67_69'
            pop.loc[pop['age'].isin(list(range(70, 75))), ['age_cat']] = '70_74'
            pop.loc[pop['age'].isin(list(range(75, 80))), ['age_cat']] = '75_79'
            pop.loc[pop['age'].isin(list(range(80, 85))), ['age_cat']] = '80_84'
            pop.loc[pop['age'].isin(list(range(85, 103))), ['age_cat']] = '85_99'

            pop = pd.DataFrame(pop['persons'].groupby([pop['yr'], pop['age_cat'], pop['sex'], pop['race_ethn']]).sum())
            print pop.head()
            pop.to_hdf('temp/data.h5', 'pop', mode='a')


class LaborForceParticipationRates(luigi.Task):

    def requires(self):
        return EmpPopulation()

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        lfpr = extract.create_df('lfp_rates', 'lfp_rates_table', index=['yr', 'age_cat', 'sex', 'race_ethn'])
        lfpr.to_hdf('temp/data.h5', 'lfpr', mode='a')


class LaborForce(luigi.Task):

    def requires(self):
        return LaborForceParticipationRates()

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        pop = pd.read_hdf('temp/data.h5', 'pop')
        lfpr = pd.read_hdf('temp/data.h5', 'lfpr')
        labor_force = pop.join(lfpr)
        labor_force['labor_force'] = (labor_force['persons'] * labor_force['lfpr']).round()

        labor_force2 = labor_force.reset_index(drop=False)

        print pd.DataFrame(labor_force2[['persons', 'labor_force']].groupby([labor_force2['yr']]).sum())
        labor_force = labor_force.iloc[~labor_force.index.get_level_values('age_cat').isin(['00_04', '05_09', '10_14'])]
        labor_force.to_hdf('temp/data.h5', 'labor_force', mode='a')


class CohortUrRate(luigi.Task):

    def requires(self):
        return LaborForce()

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        cohort_ur = extract.create_df('cohort_ur', 'cohort_ur_table', index=['yr', 'age_cat', 'sex', 'race_ethn'])
        cohort_ur.to_hdf('temp/data.h5', 'cohort_ur', mode='a')
        yearly_ur = extract.create_df('yearly_ur', 'yearly_ur_table', index=['yr'])
        yearly_ur.to_hdf('temp/data.h5', 'yearly_ur', mode='a')


class WorkForce(luigi.Task):

    def requires(self):
        return CohortUrRate()

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        labor_force = pd.read_hdf('temp/data.h5', 'labor_force')
        cohort_ur = pd.read_hdf('temp/data.h5', 'cohort_ur')
        yearly_ur = pd.read_hdf('temp/data.h5', 'yearly_ur')

        work_force = labor_force.join(cohort_ur)
        work_force['unemployed'] = (work_force['labor_force'] * work_force['ur2']).round()

        computed_ur = work_force.reset_index(drop=False)

        computed_ur = pd.DataFrame(computed_ur[['labor_force', 'unemployed']].groupby([computed_ur['yr']]).sum())
        computed_ur['computed_ur'] = (computed_ur['unemployed'] / computed_ur['labor_force'])
        computed_ur = computed_ur.join(yearly_ur)
        computed_ur['adjustment'] = (computed_ur['ur1'] / computed_ur['computed_ur'])
        print computed_ur

        work_force = work_force.join(computed_ur['adjustment'])
        work_force['unemployed'] = (work_force['unemployed'] * work_force['adjustment']).round()
        work_force['work_force'] = (work_force['labor_force'] - work_force['unemployed'])
        work_force.to_hdf('temp/data.h5', 'work_force', mode='a')

        # Code to check if after adjustment ur matches target
        '''
        computed_ur = work_force.reset_index(drop=False)

        computed_ur = pd.DataFrame(computed_ur[['labor_force', 'unemployed']].groupby([computed_ur['yr']]).sum())
        computed_ur['computed_ur'] = (computed_ur['unemployed'] / computed_ur['labor_force'])
        print computed_ur
        '''


class LocalWorkForce(luigi.Task):

    def requires(self):
        return WorkForce()

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        out_commuting = extract.create_df('out_commuting', 'out_commuting_table', index=['yr'])
        work_force = pd.read_hdf('temp/data.h5', 'work_force')
        work_force = work_force.reset_index(drop=False)
        work_force = pd.DataFrame(work_force[['labor_force', 'unemployed', 'work_force']].groupby([work_force['yr']]).sum())
        work_force = work_force.join(out_commuting)
        work_force['work_force_outside'] = (work_force['work_force'] * work_force['wtlh_lh']).round()
        work_force['work_force_local'] = (work_force['work_force'] - work_force['work_force_outside']).round()
        work_force.to_hdf('temp/data.h5', 'local_work_force', mode='a')


class Jobs(luigi.Task):

    def requires(self):
        return WorkForce()

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        #in_commuting = extract.create_df('in_commuting', 'in_commuting_table', index=['yr'])

        '''
        work_force = pd.read_hdf('temp/data.h5', 'work_force')
        work_force = work_force.reset_index(drop=False)
        work_force = pd.DataFrame(work_force[['labor_force', 'unemployed', 'work_force']].groupby([work_force['yr']]).sum())
        work_force = work_force.join(out_commuting)
        work_force['work_force_outside'] = (work_force['work_force'] * work_force['wtlh_lh']).round()
        work_force['work_force_local'] = (work_force['work_force'] - work_force['work_force_outside']).round()
        work_force.to_hdf('temp/data.h5', 'local_work_force', mode='a')
        '''

if __name__ == '__main__':
    os.makedirs('temp')
    luigi.run(main_task_cls=LocalWorkForce)
    shutil.rmtree('temp')

    #os.system("bokeh serve bokeh_graphs.py")

