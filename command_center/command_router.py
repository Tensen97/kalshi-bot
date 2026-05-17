SAFE_COMMANDS = {
    "status",
    "summarize",
    "show_risk",
    "show_external",
    "show_recommendations",
    "show_personality",
    "show_probability",
    "show_websocket",
}

FORBIDDEN_COMMANDS = {
    "place_trade",
    "change_strategy",
    "disable_safety",
    "modify_capital",
    "auto_mutate",
}


class CommandRouter:
    def __init__(self, logger=None):
        self.logger = logger or (lambda prefix, **fields: None)

    def route(self, command, state=None):
        state = state or {}
        command = str(command or "").strip().lower()
        if command in FORBIDDEN_COMMANDS:
            self.logger("[COMMAND-REJECTED]", command=command, reason="forbidden")
            return {
                "ok": False,
                "command": command,
                "reason": "forbidden",
                "message": "Command rejected: forbidden for Realm XXVII.",
            }
        if command not in SAFE_COMMANDS:
            self.logger("[COMMAND-REJECTED]", command=command, reason="unknown")
            return {
                "ok": False,
                "command": command,
                "reason": "unknown_command",
                "message": "Command rejected: unknown command.",
            }
        self.logger("[SAFE-COMMAND]", command=command)
        if command == "status":
            return {"ok": True, "command": command, "data": state}
        if command == "summarize":
            return {"ok": True, "command": command, "data": state.get("summary_text") or state.get("summary") or ""}
        if command == "show_risk":
            return {"ok": True, "command": command, "data": state.get("risk", {})}
        if command == "show_external":
            return {"ok": True, "command": command, "data": state.get("external", {})}
        if command == "show_recommendations":
            return {"ok": True, "command": command, "data": state.get("recommendations", [])}
        if command == "show_personality":
            return {"ok": True, "command": command, "data": state.get("personality", {})}
        if command == "show_probability":
            return {"ok": True, "command": command, "data": state.get("probability", {})}
        if command == "show_websocket":
            return {"ok": True, "command": command, "data": state.get("websocket", {})}
        return {"ok": False, "command": command, "reason": "unhandled"}
