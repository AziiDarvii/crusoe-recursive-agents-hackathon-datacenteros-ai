"""Simulation and agent constants.

Every physical value here is a documented *simulation assumption* chosen to be
plausible for an air-cooled 8-GPU node per rack. None of it is measured Crusoe data.
"""
from dataclasses import dataclass

# --- Electrical model -------------------------------------------------------
GPU_IDLE_KW = 0.07          # idle draw per GPU
GPU_MAX_KW = 0.70           # board power at 100% utilization (H100-SXM class)
RACK_BASE_KW = 2.5          # CPUs, NICs, fans, top-of-rack switch
UPS_LOSS_FRACTION = 0.04    # UPS / distribution losses as a fraction of IT power
FACILITY_MISC_KW = 4.0      # lighting, controls, security (fixed)

# --- Thermal model (steady state per time step) ----------------------------
# outlet = inlet + P_it / AIRFLOW_KW_PER_C
# inlet  = supply + RECIRC_C_PER_KW * max(0, P_it - effective_cooling_kw)
AIRFLOW_KW_PER_C = 0.40     # m_dot * c_p of rack airflow, kW per degC of rise
RECIRC_C_PER_KW = 4.0       # inlet rise per kW of heat the CRAH cannot remove
INLET_LIMIT_C = 27.0        # ASHRAE A1 recommended upper inlet bound
OUTLET_LIMIT_C = 45.0       # hot-aisle exhaust limit used by this simulation
THERMAL_MARGIN_C = 3.0      # soft margin used by the thermal-risk objective term

# --- Cooling efficiency ----------------------------------------------------
# COP degrades once a cooling unit runs above 80% of its effective capacity.
COP_NOMINAL = 3.5
COP_DEGRADE_KNEE = 0.8
COP_DEGRADE_SLOPE = 1.0
COP_MIN = 1.5

# --- Workload operations ---------------------------------------------------
MIGRATION_RESTORE_H = 0.25  # checkpoint + restore time after a migration
RESUME_OVERHEAD_H = 0.25    # restore time when a deferred job resumes
MIN_POWER_CAP = 0.6         # lowest simulated GPU power cap fraction
POWER_CAP_PERF_EXPONENT = 0.5  # throughput = cap ** exponent (sub-linear)

# --- Agent budget defaults -------------------------------------------------
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_MAX_DEPTH = 4
DEFAULT_MAX_LLM_CALLS = 16
DEFAULT_PACING_S = 0.35     # UI pacing between agent steps; 0 for benchmarks/tests

CRUSOE_BASE_URL = "https://api.inference.crusoecloud.com/v1"
CRUSOE_DEFAULT_MODEL = "nvidia/Nemotron-3-Nano-30B-A3B"
LLM_TIMEOUT_S = 30.0
LLM_RETRIES = 2


@dataclass(frozen=True)
class ObjectiveWeights:
    """Weights for the optimizer objective (lower cost is better)."""
    energy: float = 0.2        # per kWh over the horizon
    thermal: float = 2.0       # per (degC over soft margin)^2
    utilization: float = 0.3   # per percentage point of idle usable GPU capacity
    deadline: float = 10.0     # per priority-weighted missed deadline
    migration: float = 1.0     # per GPU moved / deferred / capped


@dataclass(frozen=True)
class AgentConfig:
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    max_depth: int = DEFAULT_MAX_DEPTH
    max_llm_calls: int = DEFAULT_MAX_LLM_CALLS
    pacing_s: float = DEFAULT_PACING_S
    use_cross_run_memory: bool = False
    weights: ObjectiveWeights = ObjectiveWeights()
