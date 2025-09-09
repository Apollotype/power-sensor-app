# Общие константы/SCPI, как в исходнике
DEFAULT_BACKENDS = ["@py", ""]
POLL_PERIOD_S = 5
READ_TERM = "\n"
WRITE_TERM = "\n"

SCPI_QUERY_POWER = "MEAS:POW?"
SCPI_ZERO        = "SENS:POW:ZERO:IMM"
SCPI_QUERY_FREQ  = "SENS:FREQ?"
SCPI_SET_FREQ    = "SENS:FREQ {freq}"

# Можно оставить пустым, либо задать адрес по умолчанию
PREFERRED_USB = ""  # пример: "USB0::...::INSTR" или "FAKE"
