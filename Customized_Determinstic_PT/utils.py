import numpy as np
import pandas as pd
import os
import json
import importlib.resources
import idaes.logger as idaeslog

_logger = idaeslog.getLogger(__name__)

def read_gmlc_gen(csv_path=None, type="gen_csv"):
    '''
    if the csv_path is provided, read it. Otherwise read from dispatches_sample_data
    '''
    if csv_path:
        df = pd.read_csv(csv_path)
    else:
        with importlib.resources.files('dispatches_sample_data.rts_gmlc.SourceData').joinpath(type).open('r') as f:
            df = pd.read_csv(f)

    return df


def _prescient_output_to_df(file_name):
    '''Helper for loading data from Prescient output csv.
        Combines Datetimes into single column.
    '''
    df = pd.read_csv(file_name)
    df['Datetime'] = \
        pd.to_datetime(df['Date']) + \
        pd.to_timedelta(df['Hour'], 'hour') + \
        pd.to_timedelta(df['Minute'], 'minute')
    df.drop(columns=['Date','Hour','Minute'], inplace=True)
    # put 'Datetime' in front
    cols = df.columns.tolist()
    cols = cols[-1:]+cols[:-1]
    
    return df[cols]


def get_gdf(directory, generator_file_name, generator_name, dispatch_name):
    gdf = _prescient_output_to_df(os.path.join(directory, generator_file_name))
    gdf = gdf[gdf["Generator"] == generator_name][["Datetime", dispatch_name, dispatch_name + " DA"]]
    gdf.set_index("Datetime", inplace=True)
    gdf.rename(columns={dispatch_name: "Dispatch", dispatch_name + " DA": "Dispatch DA"}, inplace=True)

    return gdf


def summarize_results(base_directory, generator_name, bus_name, output_directory):
    """
    Summarize Prescient runs for a single generator
    Args:
        base_directory (str) : the base directory name (without index)
        generator_name (str) : The generator name to get the dispatch for. Looks in thermal_gens.csv and then renewable_gens.csv.
        bus_name (str) : The bus to get the LMPs for.
        output_directory (str) : The location to write the summary files to.
    Returns:
        None
    """

    # param_file = os.path.join(output_directory, "sweep_parameters.csv")

    # figure out if renewable or thermal or virtual
    generator_file_names = ("thermal_detail.csv", "renewables_detail.csv", "virtual_detail.csv")
    dispatch_name_map = { "thermal_detail.csv" : "Dispatch",
                          "renewables_detail.csv" : "Output",
                          "virtual_detail.csv" : "Output",
                        }

    def _get_gen_df(generator_name):
        for generator_file_name in generator_file_names:
            gdf = pd.read_csv(os.path.join(base_directory, generator_file_name))["Generator"]
            if generator_name in gdf.unique():
                return generator_file_name
        else: # no break
            raise RuntimeError("Could not find output for generator "+generator_name)

    generator_file_name = _get_gen_df(generator_name)
    dispatch_name = dispatch_name_map[generator_file_name]


    # if not os.path.isfile(os.path.join(directory, "overall_simulation_output.csv")):
    #     raise Exception(f"For index {idx}, the simulation did not complete!")

    gdf = get_gdf(base_directory, generator_file_name, generator_name, dispatch_name)
    df_list = [gdf]
    RT_names = [gdf.columns[0]]
    DA_names = [gdf.columns[1]]


    bdf = _prescient_output_to_df(os.path.join(base_directory, "bus_detail.csv"))
    bdf = bdf[bdf["Bus"] == bus_name][["Datetime","LMP","LMP DA"]]
    bdf.set_index("Datetime", inplace=True)
    df_list.append(bdf)
    RT_names.append(bdf.columns[0])
    DA_names.append(bdf.columns[1])

    odf = pd.concat(df_list, axis=1)[[*RT_names,*DA_names]]
    odf.to_csv(os.path.join(output_directory, f"{base_directory}.csv"))

    return


def make_lmp_csv(lmp_path, bus_details_path, bus_name):
    bdf = _prescient_output_to_df(bus_details_path)
    bdf = bdf[bdf["Bus"] == bus_name][["Datetime","LMP","LMP DA"]]
    bdf.set_index("Datetime", inplace=True)

    if lmp_path == None:
        _logger.info("Empty path, make a new df")
        bdf = bdf.rename(columns={'LMP': f'{bus_name}_LMP', "LMP DA": f'{bus_name}_LMP_DA'})
        lmp_df = bdf
        lmp_df.to_csv("Data/all_bus_lmp.csv")
    else:
        _logger.info(f"Extracting LMP for {bus_name}")
        bdf = bdf.rename(columns={"LMP": f"{bus_name}_LMP", "LMP DA": f"{bus_name}_LMP_DA"})
        lmp_df = pd.read_csv(lmp_path).set_index('Datetime')
        # check if the bus has already been read
        if (f'{bus_name}_LMP' in lmp_df.columns) or (f'{bus_name}_LMP_DA' in lmp_df.columns):
            _logger.info("Bus LMP already exists.")
        else:
            bdf_aligned = bdf.reindex(lmp_df.index)
            lmp_df_merge = pd.concat([lmp_df, bdf_aligned], axis=1)
            lmp_df_merge.to_csv(lmp_path)
    
    return


def _read_json():
    '''
    read the json file to get the cost curve.
    '''
    json_path = os.path.join(os.getcwd(), 'Data', 'rtsgmle_gen_linear_cost_curve.json')
    with open(json_path, 'rb') as f:
        gen_param_dict = json.load(f)
    return gen_param_dict


def sep_fossil_renew(df):
    ''''
    Separate the fossil and renewable generators in the df
    '''
    # extract the CT, CC, STEAM, WIND, PV units
    target_fossil_gen = []
    target_renew_gen = []
    for idx, row in df.iterrows():
        if row['Unit Type'] in ['CT', 'CC', 'STEAM']:
            target_fossil_gen.append(row)
        if row['Unit Type'] in ['PV', 'WIND']:
            target_renew_gen.append(row)
    df_fossil = pd.DataFrame(target_fossil_gen)
    df_renew = pd.DataFrame(target_renew_gen)

    return df_fossil, df_renew


def _read_df(df_type, renew=False):
    gen_param_dict = _read_json()

    # get the bus id and name
    bus_path = os.path.join(os.getcwd(), "..", 'rts-gmlc', "RTS-GMLC", "RTS_Data", "SourceData", "bus.csv")
    df_bus = read_gmlc_gen(csv_path=bus_path)

    result_dict = {}
    for idx, row in df_type.iterrows():
        gen_name = row['GEN UID']
        gen_dict = {}
        gen_dict['name'] = gen_name
        bus_id= row['Bus ID']
        gen_dict['bus_name'] = df_bus[df_bus["Bus ID"]==bus_id]["Bus Name"].to_list()[0]
        gen_dict['gen_type'] = row['Unit Type']
        gen_dict['max_p'] = row['PMax MW']
        gen_dict['min_p'] = row['PMin MW']
        gen_dict['ramp'] = row['Ramp Rate MW/Min'] * 60    # ramp rate should be MW/hr
        gen_dict['fuel_p'] = row['Fuel Price $/MMBTU'] # ??? need to be updated by Kyle's side
        gen_dict['min_down_time'] = int(np.round(row['Min Down Time Hr'], 0))    # should be rounded to an integer
        gen_dict['min_up_time'] = int(np.round(row['Min Up Time Hr'], 0))    # should be rounded to an integer
        gen_dict['start_up_time_hot'] = int(np.round(row['Start Time Hot Hr'], 0)) # ???
        gen_dict['start_up_time_warm'] = int(np.round(row['Start Time Warm Hr'], 0)) # ???
        gen_dict['start_up_time_cold'] = int(np.round(row['Start Time Cold Hr'], 0)) # ???
        gen_dict['start_heat_hot'] = row['Start Heat Hot MBTU'] # ???
        gen_dict['start_heat_warm'] = row['Start Heat Warm MBTU'] # ???
        gen_dict['start_heat_cold'] = row['Start Heat Cold MBTU'] # ???
        if not renew:
            gen_dict['cost_curve'] = gen_param_dict[gen_name]
        else:
            gen_dict['cost_curve'] = {'slope': 0, 'intercept': 0} # if it is a renewable generator, set the cost curve to 0

        result_dict[gen_name] = gen_dict
    
    return result_dict

def save_gen_data(df, save_opt=False):
    df_fossil, df_renew = sep_fossil_renew(df)
    fossil_dict = _read_df(df_fossil, renew=False)
    renew_dict = _read_df(df_renew, renew=True)

    # save to json files
    if save_opt:
        all_gen_dict = {}
        all_gen_dict['fossil'] = fossil_dict
        all_gen_dict['renew'] = renew_dict
        gen_dict_path = os.path.join('Data', 'gen_dict.json')
        with open(gen_dict_path, "w") as f:
            json.dump(all_gen_dict, f)

        print("Successfully saved generator parameters to json files")

    return fossil_dict, renew_dict


# check the generator types to decide its startup types
def gen_startup_cost(gen_dict):
    '''
    Get the startup costs of the generator based on the generator parameters
    
    Args:
        gen_dict: a dictionary that stores the parameter of the generator
    
    Returns:
        start_up_cost:  a dictionary that stores the costs fo the generator
    '''
    fuel_p = gen_dict['fuel_p']
    start_heat_hot = gen_dict['start_heat_hot']
    start_heat_warm = gen_dict['start_heat_warm']
    start_heat_cold = gen_dict['start_heat_cold']
    
    start_up_cost_hot = start_heat_hot*fuel_p
    start_up_cost_warm = start_heat_warm*fuel_p
    start_up_cost_cold = start_heat_cold*fuel_p
    start_up_cost = {'hot': start_up_cost_hot, 'warm': start_up_cost_warm, 'cold': start_up_cost_cold}
    
    return start_up_cost


def read_rolling_horizon_params(json_path=None):
    '''
    Read the rolling horizon parameters from the json file
    '''
    with open(json_path, 'rb') as f:
        rolling_horizon_result = json.load(f)

    return rolling_horizon_result

def check_optimal_status(res_dict):
    """
    Check the optimal status of the rolling horizon results
    """
    non_optimal = []
    for key in res_dict.keys():
        if res_dict[key]['TerminationCondition'] != 'optimal':
            non_optimal.append(key)
    if non_optimal:
        _logger.warning(f"Totally {len(non_optimal)} periods are not optimal.")

    return non_optimal

def calculate_total_profit(res_dict, name="ActualProfit"):
    """
    Calculate the total actual profit from the rolling horizon results
    """
    total_profit = 0
    for key in res_dict.keys():
        total_profit += res_dict[key][name]
    
    return total_profit

def calculate_total_startup_shutdown(res_dict, gen_name):
    """
    Calculate the total number of startup and shutdown from the rolling horizon results
    """
    total_startup = 0
    total_shutdown = 0
    for key in res_dict.keys():
        total_startup += sum(res_dict[key][gen_name]["OperationVariables_startup"]["1"].values())
        total_shutdown += sum(res_dict[key][gen_name]["OperationVariables_shutdown"]["1"].values())

    return total_startup, total_shutdown