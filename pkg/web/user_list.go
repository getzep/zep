package web

import (
	"context"

	"github.com/getzep/zep/pkg/models"
)

type UserList struct {
	UserStore  models.UserStore
	Users      []*models.User
	TotalCount int
	Offset     int
	Cursor     int64
}

//func (u *UserList) Next() bool {
//	if u.Cursor+u.Offset < u.TotalCount {
//		u.Cursor += u.Offset
//		return true
//	}
//	return false
//}
//
//func (u *UserList) Prev() bool {
//	if u.Cursor-u.Offset >= 0 {
//		u.Cursor -= u.Offset
//		return true
//	}
//	return false
//}

func (u *UserList) Get(ctx context.Context) error {
	users, err := u.UserStore.ListAll(ctx, u.Cursor, u.Offset)
	if err != nil {
		return err
	}
	u.Users = users

	return nil
}
