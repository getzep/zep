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
		Name: "Dashboard",
		Path: "/admin",
		Icon: DashboardIcon,
	},
	{
		Name: "Users",
		Path: "/admin/users",
		Icon: UsersIcon,
	},
	{
		Name: "Sessions",
		Path: "/admin/sessions",
		Icon: SessionsIcon,
	},
	{
		Name: "Collections",
		Path: "/admin/collections",
		Icon: CollectionsIcon,
	},
	{
		Name: "Settings",
		Path: "javascript:;",
		Icon: SettingsIcon,
	},
}
