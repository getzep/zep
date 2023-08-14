package llms

import (
	"math"
	"testing"
)

func TestFloat64ToFloat32Matrix(t *testing.T) {
	in := [][]float64{
		{1.23, 4.56, 7.89},
		{0.12, 3.45, 6.78},
	}

	out := Float64ToFloat32Matrix(in)

	if len(out) != len(in) {
		t.Errorf("Expected outer length %v but got %v", len(in), len(out))
	}

	for i := range in {
		if len(out[i]) != len(in[i]) {
			t.Errorf("Expected inner length %v but got %v", len(in[i]), len(out[i]))
		}

		for j, v := range in[i] {
			if math.Abs(float64(out[i][j])-v) > 1e-6 {
				t.Errorf("Expected %v but got %v", v, out[i][j])
			}
		}
	}
}
