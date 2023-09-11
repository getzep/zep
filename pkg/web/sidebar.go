package web

import "html/template"

type MenuItem struct {
	Name      string
	Path      string
	Icon      template.HTML // SVG icon as a string
	SubItems  []SubMenuItem
	ContentID string
}

type SubMenuItem struct {
	Name      string
	URL       string
	ContentID string
}

var menuItems = []MenuItem{
	{
		Name:      "Dashboard",
		Path:      "/admin/dashboard",
		Icon:      template.HTML(DashboardIcon),
		ContentID: "#dashboard",
	},
	{
		Name:      "Users",
		Path:      "/admin/users",
		Icon:      template.HTML(UsersIcon),
		ContentID: "#users",
		//	{
		//		Name: "Sub Menu 2",
		//		Path:  "javascript:;",
		//	},
		//},
	},
	{
		Name:      "Sessions",
		Path:      "/admin/sessions",
		ContentID: "#sessions",
	},
	{
		Name: "Collections",
		Path: "javascript:;",
		Icon: template.HTML(CollectionsIcon),
	},
	{
		Name: "Settings",
		Path: "javascript:;",
		Icon: template.HTML(DashboardIcon),
	},
}
