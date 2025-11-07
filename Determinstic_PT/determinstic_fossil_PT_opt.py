import pyomo.environ as pyo
import idaes.logger as idaeslog
from idaes.apps.grid_integration import DesignModel, OperationModel
from idaes.apps.grid_integration import PriceTakerModel

_logger = idaeslog.getLogger(__name__)

def build_gen_design_model(m, params):
    '''
    build generator design models. 
    We do not do design optimization in this work so the maximun capacity is a parameter.  
    
    Args:
        m: pyomo model.
        params: dictionary that stores the generator parameters.

    Returns:
        None
    '''
    # if fom and capex not provide, just set them to 0
    if "capex" not in list(params.keys()):
        params["capex"] = 0

    if "fom" not in list(params.keys()):
        params["fom"] = 0

    m.gen_capacity = pyo.Param(
        initialize=params['max_p'],
        mutable=True,
        doc="Maxium capacity of the generator [in MW]",
    )
    m.capex = pyo.Expression(
        expr=m.gen_capacity * params["capex"],
        )
    
    m.fom = pyo.Expression(
        expr=m.gen_capacity * params["fom"]
    )
    
    return


def build_fossil_gen_operation_model(m, params):
    """
    Function that adds the fossil generator operation model

    Args:
        m: Pyomo Block
        params: dictionary that stores the generator parameters.

    Returns:
        None
    """
    # the power output at each time period
    m.power = pyo.Var(
        within=pyo.NonNegativeReals,
        doc="Net power produced by NGCC at time t [in MW]",
        bounds=(0, params['max_p']),
        # doc="Output of the power at time t"
    )

    # vom is a linear function of the power
    slope = params["cost_curve"]["slope"]
    intercept = params["cost_curve"]["intercept"]
    m.vom = pyo.Expression(expr=slope * m.power + intercept * m.op_mode)

    # for the single type startup, use cold start cost.
    # cold_start_cost = params["start_heat_cold"] * params["fuel_p"]
    # m.startup_cost = pyo.Expression(expr=cold_start_cost * m.startup)
    # m.shutdown_cost = pyo.Expression(expr=0 * m.shutdown)

    # for multiple type startup, define the startup costs based on the type
    # types = ["hot", "warm", "cold"]
    cost_list = {type_: params["fuel_p"]*params["start_heat_" + type_] for type_ in types}
    # m.startup_cost = pyo.Expression(expr=sum(m.startup_type_vars[k]*cost_list[k] for k in m.startup_type_vars))
    m.shutdown_cost = pyo.Expression(expr=0 * m.shutdown)

    return


def build_fossil_gen_flowsheet(m, params):
    """Builds the fossil generator flowsheet"""
    types = ["hot", "warm", "cold"]
    # startup_types = {type_: params["start_up_time_" + type_] for type_ in types}
    setattr(m, 
            "gen_" + params["name"],
            OperationModel(
                model_func=build_fossil_gen_operation_model,
                model_args={"params": params},
                # startup_types=startup_types
        )
    )

    m.power_to_grid = pyo.Var(within=pyo.NonNegativeReals)
    m.calculate_power_to_grid = pyo.Constraint(
        expr=m.power_to_grid == getattr(m, "gen_" + params["name"]).power
    )
    m.elec_revenue = pyo.Expression(expr=getattr(m, "gen_"+params["name"]).LMP * m.power_to_grid)


def fix_dispatch(m, data):
    """Adds constraints to fix the dispatch value"""

    # Add a constraint to each flowsheet instance to fix dispatch
    for p in m.period:
        m.period[p].constrain_dispatch = pyo.Constraint(
            expr=m.period[p].power_to_grid == data[p[1]-1]
        )


def determinstic_fossil_profit_opt(params, lmp_data, dispatch_data, configuration=None, fixing_dispatch=False):
    """Builds and returns an instance of the price-taker model"""
    m = PriceTakerModel()

    # Appending the data to the model
    m.append_lmp_data(lmp_data=lmp_data)

    # Build design models and fix the capacity
    m.gen_design = DesignModel(
        model_func=build_gen_design_model,
        model_args={"params": params},
    )

    # Build multiperiod operation model
    m.build_multiperiod_model(
        flowsheet_func=build_fossil_gen_flowsheet,
        flowsheet_options={
            "params": params,
        },
    )

    # Add operation limits
    m.add_capacity_limits(
        op_block_name="gen_" + params["name"],
        commodity="power",
        capacity=params["max_p"],
        op_range_lb=params["min_p"]/params["max_p"],
    )

    # Add minimum uptime-downtime constraints on the unit
    # types = ["hot", "warm", "cold"]
    # startup_transition_time = {type_: params["start_up_time_" + type_] for type_ in types}
    m.add_startup_shutdown(
        op_block_name="gen_" + params["name"],
        minimum_up_time=params["min_up_time"],
        minimum_down_time=params["min_down_time"],
        # startup_transition_time=startup_transition_time,
    )

    # Add ramping constraints on the unit
    m.add_ramping_limits(
        op_block_name="gen_" + params["name"],
        commodity="power",
        capacity=params["max_p"],
        startup_rate=params["min_p"]/params["max_p"],
        shutdown_rate=params["max_p"]/params["max_p"],
        rampup_rate=1,
        rampdown_rate=1,
    )

    # Build, hourly cashflows, overall cashflows, and the objective function
    m.add_hourly_cashflows(
        revenue_streams=["elec_revenue"],
        operational_costs=["vom", "startup_cost"],
    )

    m.add_overall_cashflows(corporate_tax_rate=0)
    m.add_objective_function(objective_type="net_profit")

    if fixing_dispatch:
        fix_dispatch(m, dispatch_data)

    return m