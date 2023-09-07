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
		URL:  "javascript:;",
		Icon: template.HTML(DashboardIcon), //gosec:ignore
	},
	{
		Name: "Users",
		URL:  "javascript:;",
		Icon: template.HTML(UsersIcon), //gosec:ignore
		SubItems: []SubMenuItem{
			{
				Name: "Sub Menu 1",
				URL:  "javascript:;",
			},
			{
				Name: "Sub Menu 2",
				URL:  "javascript:;",
			},
		},
	},
	{
		Name: "Collections",
		URL:  "javascript:;",
		Icon: template.HTML(CollectionsIcon), //gosec:ignore
	},
	{
		Name: "Settings",
		URL:  "javascript:;",
		Icon: template.HTML(DashboardIcon), //gosec:ignore
	},
}
