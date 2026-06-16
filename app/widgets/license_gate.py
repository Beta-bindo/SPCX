"""授权门禁：启动时验证授权服务器状态，未通过则弹窗收集申请信息并轮询审核结果。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from app.core.license.client import LicenseError
from app.core.license.service import LicenseService
from app.core.license.store import effective_server_url


class _LicenseWorker(QThread):
    """在子线程执行阻塞式授权 HTTP 调用，避免卡住 UI 线程。"""

    finished_ok = Signal(str, str)  # (动作, 消息)
    failed = Signal(str, str)       # (动作, 错误)

    def __init__(self, action: str, fn):
        super().__init__()
        self._action = action
        self._fn = fn

    def run(self) -> None:
        try:
            message = self._fn() or ""
            self.finished_ok.emit(self._action, message)
        except LicenseError as exc:
            self.failed.emit(self._action, str(exc))
        except Exception as exc:
            self.failed.emit(self._action, f"未知错误：{exc}")


class LicenseGateDialog(QDialog):
    """首次启动或待审核时显示的授权门禁。"""

    def __init__(self, service: LicenseService, parent=None):
        super().__init__(parent)
        self.service = service
        self.quit_requested = False
        self._worker: _LicenseWorker | None = None
        self.setWindowTitle("交易助手 · 授权验证")
        self.setMinimumWidth(460)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setSpacing(12)

        intro = QLabel(
            "本软件需经管理员审核后方可使用。\n"
            "请填写申请信息，审核通过后即可连接 BA / Exness 进行监控与交易。"
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        form = QFormLayout()
        self.display_name = QLineEdit(self.service.client.state.display_name)
        self.contact = QLineEdit(self.service.client.state.contact)
        self.contact.setPlaceholderText("11位大陆手机号")
        self.contact.setMaxLength(11)
        self.note = QTextEdit(self.service.client.state.note)
        self.note.setMaximumHeight(72)
        device_id = self.service.client.state.device_id
        device_lbl = QLabel(device_id)
        device_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        copy_btn = QPushButton("复制机器码")
        copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(device_id))
        device_row = QHBoxLayout()
        device_row.addWidget(device_lbl, stretch=1)
        device_row.addWidget(copy_btn)
        form.addRow("机器码", device_row)
        form.addRow("昵称", self.display_name)
        form.addRow("联系方式", self.contact)
        form.addRow("备注", self.note)
        root.addLayout(form)

        btn_row = QHBoxLayout()
        self.submit_btn = QPushButton("提交申请")
        self.submit_btn.setObjectName("primaryButton")
        self.refresh_btn = QPushButton("刷新状态")
        self.exit_btn = QPushButton("退出")
        btn_row.addWidget(self.submit_btn)
        btn_row.addWidget(self.refresh_btn)
        btn_row.addWidget(self.exit_btn)
        root.addLayout(btn_row)

        self.submit_btn.clicked.connect(self._on_submit)
        self.refresh_btn.clicked.connect(self._on_refresh)
        self.exit_btn.clicked.connect(self._on_exit)
        self.service.status_changed.connect(self._apply_status)
        self._apply_status(
            self.service.client.state.status,
            self.service.client.state.message,
            auto_accept=False,
        )

    def _set_busy(self, busy: bool, hint: str = "") -> None:
        self.submit_btn.setEnabled(not busy)
        self.refresh_btn.setEnabled(not busy)
        self.exit_btn.setEnabled(not busy)
        if busy:
            self.refresh_btn.setEnabled(False)
            text = f"当前状态：{hint}" if hint else "当前状态：处理中…"
            self.status_label.setText(text)
        else:
            self._apply_status(self.service.client.state.status, self.service.client.state.message)

    def _apply_status(self, status: str, message: str, *, auto_accept: bool = True) -> None:
        """根据授权状态刷新提示文案/按钮；approved 时自动关闭门禁。"""
        if (
            self._worker
            and self._worker.isRunning()
            and status != "approved"
        ):
            return
        labels = {
            "pending": "待审核",
            "approved": "已通过",
            "rejected": "已拒绝",
            "disabled": "已停用",
            "unknown": "未注册",
        }
        text = f"当前状态：{labels.get(status, status)}"
        if message:
            text += f"\n{message}"
        self.status_label.setText(text)
        self.refresh_btn.setEnabled(
            status in ("pending", "approved", "unknown", "rejected", "disabled")
        )
        if status == "approved" and auto_accept:
            self.accept()

    def _sync_server_url(self) -> None:
        url = effective_server_url()
        self.service.client.state.server_url = url
        from app.core.license.store import save_license

        save_license(self.service.client.state)

    def _start_worker(self, action: str, fn, busy_hint: str) -> None:
        """启动后台授权任务（注册/刷新），期间禁用按钮。"""
        if self._worker and self._worker.isRunning():
            return
        self._set_busy(True, busy_hint)
        self._worker = _LicenseWorker(action, fn)
        self._worker.finished_ok.connect(self._on_worker_ok)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_worker_finished(self) -> None:
        self._worker = None

    def _on_worker_ok(self, action: str, message: str) -> None:
        self._set_busy(False)
        if action == "register":
            msg = message or self.service.client.state.message or "申请已提交，等待管理员审核"
            if self.service.is_approved:
                self.accept()
                return
            QMessageBox.information(self, "提交成功", msg)
            self._start_worker("refresh", self._do_refresh, "正在刷新状态…")
        elif action == "refresh":
            if self.service.is_approved:
                self.accept()
                return
            msg = self.service.client.state.message
            if msg:
                QMessageBox.information(self, "授权状态", msg)

    def _on_worker_failed(self, action: str, error: str) -> None:
        self._set_busy(False)
        if action == "register":
            if self.service.client.state.status == "pending":
                QMessageBox.warning(
                    self,
                    "提交可能已成功",
                    f"{error}\n\n若管理员后台已看到您的申请，请稍后点击「刷新状态」。",
                )
            else:
                QMessageBox.critical(self, "提交失败", error)
        else:
            QMessageBox.warning(self, "连接失败", error)

    def _do_register(self) -> str:
        name = self.display_name.text().strip()
        phone = self.contact.text().strip()
        if not self._is_valid_mainland_mobile(phone):
            raise ValueError("联系方式必须是大陆 11 位手机号")
        self.service.register(name, phone, self.note.toPlainText())
        return self.service.client.state.message

    @staticmethod
    def _is_valid_mainland_mobile(phone: str) -> bool:
        """校验是否为合法的大陆 11 位手机号。"""
        return (
            len(phone) == 11
            and phone.isdigit()
            and phone[0] == "1"
            and phone[1] in "3456789"
        )

    def _do_refresh(self) -> str:
        self.service.refresh()
        return self.service.client.state.message

    def _on_submit(self) -> None:
        name = self.display_name.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请填写昵称")
            return
        phone = self.contact.text().strip()
        if not self._is_valid_mainland_mobile(phone):
            QMessageBox.warning(self, "提示", "请填写正确的 11 位大陆手机号")
            return
        self._sync_server_url()
        self._start_worker("register", self._do_register, "正在提交申请…")

    def _on_refresh(self) -> None:
        self._sync_server_url()
        self._start_worker("refresh", self._do_refresh, "正在连接服务器…")

    def _on_exit(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self.quit_requested = True
        self.reject()


def ensure_license_approved(
    parent=None, service: LicenseService | None = None
) -> LicenseService | None:
    """启动门禁：必须经服务器确认 approved；返回 None 表示用户退出。"""
    from app.core.build_config import LICENSE_REQUIRED

    if not LICENSE_REQUIRED:
        service = service or LicenseService()
        service.client.state.status = "approved"
        service.client.state.access_token = "dev-local-skip"
        service.client.state.message = "无授权版（跳过授权校验）"
        return service

    service = service or LicenseService()
    if _verify_with_server(service):
        service.start_heartbeat()
        return service

    while True:
        dlg = LicenseGateDialog(service, parent)
        code = dlg.exec()
        if _verify_with_server(service):
            service.start_heartbeat()
            return service
        if dlg.quit_requested or code != QDialog.DialogCode.Accepted:
            return None
        QMessageBox.warning(
            parent,
            "未授权",
            service.client.state.message or "尚未通过授权，请提交申请或等待管理员审核。",
        )


def _verify_with_server(service: LicenseService) -> bool:
    """授权服务器返回 approved 且令牌有效时视为通过（平台账号在交易时再校验）。"""
    try:
        service.refresh()
    except LicenseError:
        return False
    return service.is_approved
