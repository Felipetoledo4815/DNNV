import numpy as np
import tempfile
import torch.onnx

from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from dnnv.verifiers.common.base import Parameter, Verifier
from dnnv.verifiers.common.results import SAT, UNSAT, UNKNOWN

from .errors import VerinetError, VerinetTranslatorError


class VeriNet(Verifier):
    EXE = "verinet.py"
    translator_error = VerinetTranslatorError
    verifier_error = VerinetError
    parameters = {
        "max_proc": Parameter(int, help="Maximum number of processes to use."),
        "no_split": Parameter(bool, help="Whether or not to do splitting."),
    }

    def build_inputs(self, prop):
        if prop.input_constraint.num_variables > 1:
            raise self.translator_error(
                "Unsupported network: More than 1 input variable"
            )

        with tempfile.NamedTemporaryFile(
            mode="w+", suffix=".onnx", delete=False
        ) as onnx_model_file:
            op_graph = prop.suffixed_op_graph()
            op_graph.output_operations[0].b = np.hstack(
                [
                    op_graph.output_operations[0].b,
                    np.zeros(
                        op_graph.output_operations[0].b.shape,
                        dtype=op_graph.output_operations[0].b.dtype,
                    ),
                ]
            )

            torch.onnx.export(
                op_graph.as_pytorch(),
                tuple(torch.ones(shape) for shape in op_graph.input_shape),
                onnx_model_file.name,
            )

        lb = prop.input_constraint.lower_bounds[0].flatten().copy()
        ub = prop.input_constraint.upper_bounds[0].flatten().copy()

        input_bounds = np.array(list(zip(lb, ub))).reshape(
            tuple(prop.input_constraint.lower_bounds[0].shape) + (2,)
        )
        with tempfile.NamedTemporaryFile(
            mode="w+", suffix=".npy", delete=False
        ) as input_bounds_file:
            np.save(input_bounds_file.name, input_bounds)

        with tempfile.NamedTemporaryFile(
            mode="w+", suffix=".npy", delete=False
        ) as output_file:
            self._tmp_output_file = output_file
        args = (
            "verinet.py",
            onnx_model_file.name,
            input_bounds_file.name,
            "-o",
            self._tmp_output_file.name,
        )
        if "max_proc" in self.parameters and self.parameters["max_proc"] is not None:
            value = self.parameters["max_proc"]
            args += (f"--max_proc={value}",)
        if "no_split" in self.parameters and self.parameters["no_split"]:
            args += ("--no_split",)
        return args

    def parse_results(self, prop, results):
        try:
            status, cex = np.load(self._tmp_output_file.name, allow_pickle=True)
            if status == "Safe":
                return UNSAT, None
            elif status == "Unsafe":
                input_shape, input_dtype = prop.op_graph.input_details[0]
                return SAT, cex.reshape(input_shape)
            elif status == "Unknown":
                return UNKNOWN, None
            elif status == "Undecided":
                raise self.verifier_error("Undecided")
            raise self.translator_error(f"Unknown verification result: {result_str}")
        finally:
            del self._tmp_output_file