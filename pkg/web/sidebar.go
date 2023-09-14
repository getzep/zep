package web

import "html/template"

const AdminPath = "/admin"

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
		Path: AdminPath,
		Icon: DashboardIcon,
	},
	{
		Name: "Users",
		Path: AdminPath + "/users",
		Icon: UsersIcon,
	},
	{
		Name: "Sessions",
		Path: AdminPath + "/sessions",
		Icon: SessionsIcon,
	},
	{
		Name: "Collections",
		Path: AdminPath + "/collections",
		Icon: CollectionsIcon,
	},
	{
		Name: "Settings",
		Path: AdminPath + "/settings",
		Icon: SettingsIcon,
	},
}
