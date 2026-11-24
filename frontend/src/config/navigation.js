import {
  Activity,
  BarChart3,
  Bell,
  Camera,
  ClipboardList,
  FileText,
  Home,
  MapPin,
  ShieldCheck,
  UserRound,
  Users,
} from "lucide-react";

// Format: [menu label, icon, URL path, internal page name]
export const USER_NAVIGATION = [
  ["Overview", Home, "/user/dashboard", "Overview Dashboard"],
  ["Report Pothole", Camera, "/user/report", "Live Detection"],
  ["My Potholes", ClipboardList, "/user/my-potholes", "Pothole Registry"],
  ["My Pothole Tracking", ClipboardList, "/user/tracking", "Pothole Tracking"],
  ["My Detection History", Activity, "/user/detections", "Detection History"],
  ["My Profile", UserRound, "/user/profile", "My Profile"],
  ["Reports & Exports", FileText, "/user/reports", "Reports and Exports"],
];

export const ADMIN_NAVIGATION = [
  ["Global Overview", Home, "/admin/dashboard", "Overview Dashboard"],
  ["Administration", Users, "/admin/users", "User Management"],
  ["Admin Approval Requests", ShieldCheck, "/admin/requests", "Admin Requests"],
  ["Pothole Management", ClipboardList, "/admin/potholes", "Pothole Registry"],
  ["My Notifications", Bell, "/admin/notifications", "Notifications"],
  ["Detection Monitoring", Activity, "/admin/detections", "Detection Monitoring"],
  ["Global Analytics", BarChart3, "/admin/analytics", "Analytics"],
  ["Global Map", MapPin, "/admin/map", "GIS Map"],
  ["Reports & Exports", FileText, "/admin/reports", "Reports and Exports"],
  ["My Profile", UserRound, "/admin/profile", "My Profile"],
];
