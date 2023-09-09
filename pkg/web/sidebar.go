package web

import "html/template"

type MenuItem struct {
	Name     string
	URL      string
	Icon     template.HTML // SVG icon as a string
	SubItems []SubMenuItem
}

type SubMenuItem struct {
	Name string
	URL  string
}

var menuItems = []MenuItem{
	{
		Name: "Dashboard",
		URL:  "/admin/dashboard",
		Icon: template.HTML(DashboardIcon),
	},
	{
		Name: "Users",
		URL:  "/admin/users",
		Icon: template.HTML(UsersIcon),
		//SubItems: []SubMenuItem{
		//	{
		//		Name: "Sub Menu 1",
		//		URL:  "javascript:;",
		//	},
		//	{
		//		Name: "Sub Menu 2",
		//		URL:  "javascript:;",
		//	},
		//},
	},
	{
		Name: "Collections",
		URL:  "javascript:;",
		Icon: template.HTML(CollectionsIcon),
	},
	{
		Name: "Settings",
		URL:  "javascript:;",
		Icon: template.HTML(DashboardIcon),
	},
}
