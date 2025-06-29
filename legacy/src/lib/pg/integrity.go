package pg

import "github.com/uptrace/bun/driver/pgdriver"

func IsIntegrityViolation(err error) bool {
	pgErr, ok := err.(pgdriver.Error)

	return ok && pgErr.IntegrityViolation()
}
