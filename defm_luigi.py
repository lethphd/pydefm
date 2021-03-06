import luigi
import inspect, os
import pandas as pd
import time
from db import extract
from db import log
from forecast import compute
from forecast import util
import shutil
import luigi.contrib.hadoop
from pathlib import Path
from sqlalchemy import create_engine
from pysandag.database import get_connection_string
import pydefm.compute as cp
import numpy as np
from forecast import util


class Population(luigi.Task):
    year = luigi.Parameter()

    def requires(self):
        return None

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):

        my_file = Path('temp/data.h5')
        if my_file.is_file():
            print'File exists'
        else:
            db_run_id = log.new_run()
            run_id = pd.Series([db_run_id])
            run_id.to_hdf('temp/data.h5', 'run_id',  mode='a')
            pop = extract.create_df('population', 'population_table')
            pop.to_hdf('temp/data.h5', 'pop', format='table', mode='a')

            pop2 = pop[(pop['type'] == 'HHP')]
            pop2 = pop2.reset_index(drop=False)

            pop2 = pd.DataFrame(pop2['persons'].groupby([pop2['age'], pop2['race_ethn'], pop2['sex']]).sum())
            pop2.rename(columns={'persons': 'persons_sum'}, inplace=True)

            pop2 = pop.join(pop2)
            pop2['rates'] = np.where(pop2['type'].isin(['INS', 'OTH']), (pop2['persons'] / pop2['persons_sum']), 0)

            rates = pop2[['mildep', 'type', 'rates']]
            rates.to_hdf('temp/data.h5', 'ins_oth_rates', format='table', mode='a')

            engine = create_engine(get_connection_string("model_config.yml", 'output_database'))
            population_summary = []
            population_summary.append({'Year': self.year - 1,
                                       'Run_id': run_id[0],
                                       'Population': pop['persons'].sum(),
                                       'mig_out': 0,
                                       'mig_in': 0,
                                       'deaths': 0,
                                       'new_born': 0})

            summary_df = pd.DataFrame(population_summary)
            summary_df.to_sql(name='population_summary', con=engine, schema='defm', if_exists='append', index=False)
            pop['yr'] = 2010
            pop['run_id'] = db_run_id
            pop.to_sql(name='population', con=engine, schema='defm', if_exists='append', index=True)


class InMigrationRates(luigi.Task):
    year = luigi.Parameter()

    def requires(self):
        return Population(self.year)

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        mig_rates = extract.create_df('migration', 'migration_rate_table')
        mig_rates = mig_rates[['yr', 'DIN', 'FIN']]
        mig_rates.to_hdf('temp/data.h5', 'in_mig_rates', format='table', mode='a')


class OutMigrationRates(luigi.Task):
    year = luigi.Parameter()

    def requires(self):
        return Population(self.year)

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        mig_rates = extract.create_df('migration', 'migration_rate_table')
        mig_rates = mig_rates[['yr', 'DOUT', 'FOUT']]
        mig_rates.to_hdf('temp/data.h5', 'out_mig_rates', format='table', mode='a')


class DeathRates(luigi.Task):
    year = luigi.Parameter()

    def requires(self):
        return Population(self.year)

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        death_rates = extract.create_df('death', 'death_rate_table')
        death_rates.to_hdf('temp/data.h5', 'death_rates', format='table', mode='a')


class BirthRates(luigi.Task):
    year = luigi.Parameter()

    def requires(self):
        return Population(self.year)

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        birth_rates = extract.create_df('birth', 'birth_rate_table')
        birth_rates.to_hdf('temp/data.h5', 'birth_rates', format='table', mode='a')


class MigrationPopulationOut(luigi.Task):
    year = luigi.Parameter()

    def requires(self):
        return {
                'migration_rates': OutMigrationRates(self.year)
                }

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        mig_rates = pd.read_hdf('temp/data.h5', 'out_mig_rates')
        pop = pd.read_hdf('temp/data.h5', 'pop')
        pop = compute.rates_for_yr(pop, mig_rates, self.year)

        pop = pop[(pop['type'] == 'HHP') & (pop['mildep'] == 'N')]

        pop = cp.out_migrating_population(pop)
        pop.to_hdf('temp/data.h5', 'mig_out', format='table', mode='a')


class MigrationPopulationIn(luigi.Task):
    year = luigi.Parameter()

    def requires(self):
        return {
                'migration_rates': InMigrationRates(self.year)
                }

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        mig_rates = pd.read_hdf('temp/data.h5', 'in_mig_rates')
        pop = pd.read_hdf('temp/data.h5', 'pop')
        pop = compute.rates_for_yr(pop, mig_rates, self.year)
        pop = pop[(pop['type'] == 'HHP') & (pop['mildep'] == 'N')]

        pop = cp.in_migrating_population(pop)

        pop.to_hdf('temp/data.h5', 'mig_in', format='table', mode='a')


class NonMigratingPopulation(luigi.Task):
    year = luigi.Parameter()

    def requires(self):
        return {'migration_pop': MigrationPopulationOut(self.year)
                }

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        out_pop = pd.read_hdf('temp/data.h5', 'mig_out')
        pop = pd.read_hdf('temp/data.h5', 'pop')
        pop = pop.join(out_pop)
        pop.loc[pop['type'].isin(['COL', 'INS', 'MIL', 'OTH']), ['mig_Dout', 'mig_Fout']] = 0
        pop.loc[pop['mildep'].isin(['Y']), ['mig_Dout', 'mig_Fout']] = 0

        pop = cp.non_migrating_population(pop)
        pop.to_hdf('temp/data.h5', 'non_mig_pop', format='table', mode='a')


class DeadPopulation(luigi.Task):
    year = luigi.Parameter()

    def requires(self):
        return {'non_mig_pop': NonMigratingPopulation(self.year),
                'death_rates': DeathRates(self.year)
                }

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        death_rates = pd.read_hdf('temp/data.h5', 'death_rates')
        death_rates = death_rates[(death_rates['yr'] == self.year)]
        pop = pd.read_hdf('temp/data.h5', 'non_mig_pop')
        pop = pop.join(death_rates)
        pop = pop[(pop['type'] == 'HHP') & (pop['mildep'] == 'N')]

        # do we apply death rates to mil pop?
        pop = cp.dead_population(pop)
        pop.to_hdf('temp/data.h5', 'dead_pop', format='table', mode='a')


class NonMigratingSurvivedPop(luigi.Task):
    year = luigi.Parameter()

    def requires(self):
        return {'non_mig_pop': NonMigratingPopulation(self.year),
                'dead_pop': DeadPopulation(self.year)
                }

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        deaths = pd.read_hdf('temp/data.h5', 'dead_pop')
        non_mig_pop = pd.read_hdf('temp/data.h5', 'non_mig_pop')
        non_mig_pop = non_mig_pop.join(deaths, how='left')

        non_mig_pop.loc[non_mig_pop['type'].isin(['COL', 'INS', 'MIL', 'OTH']), ['deaths']] = 0
        non_mig_pop.loc[non_mig_pop['mildep'].isin(['Y']), ['deaths']] = 0

        non_mig_pop = cp.non_migrating_survived_pop(non_mig_pop)

        non_mig_pop.to_hdf('temp/data.h5', 'non_mig_survived_pop', format='table', mode='a')


class NewBornPopulation(luigi.Task):
    year = luigi.Parameter()

    def requires(self):
        return {'non_mig_population': NonMigratingPopulation(self.year),
                'birth_rates': BirthRates(self.year)
                }

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        birth_rates = pd.read_hdf('temp/data.h5', 'birth_rates')
        pop = pd.read_hdf('temp/data.h5', 'non_mig_pop')
        pop = pop[(pop['type'] == 'HHP') & (pop['mildep'] == 'N')]
        birth_rates = compute.rates_for_yr(pop, birth_rates, self.year)
        birth_rates = birth_rates[(birth_rates['yr'] == self.year)]
        random_numbers = extract.create_df('random_numbers', 'random_numbers_table')
        random_numbers = random_numbers[(random_numbers['yr'] == self.year)]
        random_numbers = random_numbers[['random_number']]
        births_per_cohort = compute.births_all(birth_rates, self.year, pop_col='non_mig_pop', rand_df=random_numbers)

        death_rates = pd.read_hdf('temp/data.h5', 'death_rates')
        death_rates = death_rates[(death_rates['yr'] == self.year)]
        # sum newborn population across cohorts
        newborn = compute.births_sum(births_per_cohort, self.year)

        newborn = newborn.join(death_rates)
        newborn['new_deaths'] = (newborn['new_born'] * newborn['death_rate']).round()
        newborn['new_born_survived'] = (newborn['new_born'] - newborn['new_deaths']).round()

        dead_pop = pd.read_hdf('temp/data.h5', 'dead_pop')
        dead_pop = dead_pop.join(newborn['new_deaths'])

        dead_pop = dead_pop.fillna(0)
        dead_pop['deaths'] = (dead_pop['deaths'] + dead_pop['new_deaths']).round()

        dead_pop = dead_pop.drop(['new_deaths'], 1)

        dead_pop.to_hdf('temp/data.h5', 'dead_pop', format='table', mode='a')

        newborn = newborn.drop(['new_deaths', 'death_rate'], 1)
        newborn.to_hdf('temp/data.h5', 'new_born', format='table', mode='a')


class AgedPop(luigi.Task):
    year = luigi.Parameter()

    def requires(self):
        return {'non_mig_survived_pop': NonMigratingSurvivedPop(self.year)
                }

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        non_mig_survived_pop = pd.read_hdf('temp/data.h5', 'non_mig_survived_pop')
        non_mig_survived_pop = cp.aged_pop(non_mig_survived_pop)
        non_mig_survived_pop.to_hdf('temp/data.h5', 'aged_pop', format='table', mode='a')


class NewPopulation(luigi.Task):
    year = luigi.Parameter()

    def requires(self):
        return {'new_born': NewBornPopulation(self.year),
                'in_mig_pop': MigrationPopulationIn(self.year)
                }

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        new_born = pd.read_hdf('temp/data.h5', 'new_born')
        mig_in = pd.read_hdf('temp/data.h5', 'mig_in')
        # sum newborn population across cohorts
        # new_born['new_born'] = new_born['persons']
        new_pop = mig_in.join(new_born)
        new_pop = new_pop.fillna(0)

        new_pop = cp.new_population(new_pop)

        new_pop.to_hdf('temp/data.h5', 'new_pop', format='table', mode='a')


class FinalPopulation(luigi.Task):
    year = luigi.Parameter()

    def requires(self):
        return {'aged_pop': AgedPop(self.year),
                'new_pop': NewPopulation(self.year)
                }

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        aged_pop = pd.read_hdf('temp/data.h5', 'aged_pop')
        new_pop = pd.read_hdf('temp/data.h5', 'new_pop')
        rates = pd.read_hdf('temp/data.h5', 'ins_oth_rates')

        pop = aged_pop.join(new_pop)
        pop = pop.fillna(0)

        pop.loc[pop['type'].isin(['COL', 'INS', 'MIL', 'OTH']), ['new_pop']] = 0
        pop.loc[pop['mildep'].isin(['Y']), ['new_pop']] = 0

        pop = pop.reset_index(drop=False)

        rates = rates.reset_index(drop=False)

        pop = pop.set_index(['age', 'race_ethn', 'sex', 'mildep', 'type'])
        rates = rates.set_index(['age', 'race_ethn', 'sex', 'mildep', 'type'])

        pop = pop.join(rates)
        pop = pop.reset_index(drop=False)
        pop = pop.set_index(['age', 'race_ethn', 'sex'])

        pop = cp.final_population(pop)
        pop.to_hdf('temp/data.h5', 'pop', format='table', mode='a')


class ExportTables(luigi.Task):
    year = luigi.Parameter()

    @property
    def priority(self):
        return 10000 - self.year

    def requires(self):
        return FinalPopulation(self.year)

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        engine = create_engine(get_connection_string("model_config.yml", 'output_database'))
        run_table = pd.read_hdf('temp/data.h5', 'run_id')
        run_id = run_table[0]
        population_summary = []  # initialize list for population by year
        pop = pd.read_hdf('temp/data.h5', 'pop')
        mig_out = pd.read_hdf('temp/data.h5', 'mig_out')
        mig_in = pd.read_hdf('temp/data.h5', 'mig_in')
        dead_pop = pd.read_hdf('temp/data.h5', 'dead_pop')
        new_born = pd.read_hdf('temp/data.h5', 'new_born')
        population_summary.append({'Year': self.year,
                                   'Run_id': run_id,
                                   'Population': pop['persons'].sum(),
                                   'mig_out': mig_out['mig_Dout'].sum() + mig_out['mig_Fout'].sum(),
                                   'mig_in': mig_in['mig_Din'].sum() + mig_in['mig_Fin'].sum(),
                                   'deaths': dead_pop['deaths'].sum(),
                                   'new_born': new_born['new_born'].sum()})

        for table in [pop, mig_out, mig_in, dead_pop, new_born]:
            # df = pd.read_hdf('temp/data.h5', table)
            table['yr'] = self.year
            table['run_id'] = run_id

        pop.to_sql(name='population', con=engine, schema='defm', if_exists='append', index=True)
        mig_out.to_sql(name='mig_out', con=engine, schema='defm', if_exists='append', index=True)
        mig_in.to_sql(name='mig_in', con=engine, schema='defm', if_exists='append', index=True)
        dead_pop.to_sql(name='dead_pop', con=engine, schema='defm', if_exists='append', index=True)
        new_born.to_sql(name='new_born', con=engine, schema='defm', if_exists='append', index=True)

        summary_df = pd.DataFrame(population_summary)
        summary_df.to_sql(name='population_summary', con=engine, schema='defm', if_exists='append', index=False)


class Iter(luigi.contrib.hadoop.JobTask):

    def requires(self):
        years = util.yaml_to_dict('model_config.yml', 'years')
        return [ExportTables(y) for y in range(years['y1'], years['yf'] + 1)]

    def output(self):
        return luigi.LocalTarget('temp/data.h5')

    def run(self):
        print 'complete'


if __name__ == '__main__':

    os.makedirs('temp')
    luigi.run(main_task_cls=Iter)
    shutil.rmtree('temp')
