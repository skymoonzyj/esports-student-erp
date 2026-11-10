"""Built-in asset category choices; group labels are never saved as categories."""

from __future__ import annotations

from app.asset_management.schemas import AssetCategoryGroup

CATEGORY_GROUPS: tuple[AssetCategoryGroup, ...] = (
    AssetCategoryGroup(
        key="computer_esports",
        label="电脑与电竞设备",
        options=[
            "台式电脑",
            "笔记本电脑",
            "显示器",
            "训练手机",
            "平板电脑",
            "键盘",
            "鼠标",
            "电竞耳机",
            "游戏手柄",
            "VR设备",
        ],
    ),
    AssetCategoryGroup(
        key="server_network",
        label="服务器与网络设备",
        options=["服务器", "NAS与存储设备", "路由器", "交换机", "无线AP", "UPS电源"],
    ),
    AssetCategoryGroup(
        key="office_teaching",
        label="办公与教学设备",
        options=["打印复印扫描设备", "投影仪", "教学大屏", "办公电话", "会议设备"],
    ),
    AssetCategoryGroup(
        key="furniture_dormitory",
        label="家具与宿舍设施",
        options=["办公桌", "办公椅", "电竞椅", "床与床垫", "衣柜与储物柜", "沙发与茶几"],
    ),
    AssetCategoryGroup(
        key="home_appliances",
        label="生活电器",
        options=["空调", "风扇", "冰箱", "洗衣机", "饮水机", "热水器", "清洁电器"],
    ),
    AssetCategoryGroup(
        key="kitchen_dining",
        label="餐厨设备",
        options=["炊事设备", "消毒设备", "商用冷藏冷冻设备", "餐桌餐椅", "餐厨器具"],
    ),
    AssetCategoryGroup(
        key="media_streaming",
        label="影音与直播设备",
        options=["摄影摄像设备", "麦克风与声卡", "音响", "灯光与直播设备"],
    ),
    AssetCategoryGroup(
        key="security_fire",
        label="安防与消防设备",
        options=["监控与录像设备", "门禁设备", "消防器材"],
    ),
    AssetCategoryGroup(
        key="maintenance_cleaning", label="维修与保洁工具", options=["维修工具", "保洁工具"]
    ),
    AssetCategoryGroup(
        key="sports_training",
        label="体育与训练器材",
        options=["健身器材", "运动器材", "训练辅助设备"],
    ),
    AssetCategoryGroup(
        key="transport_handling",
        label="交通与搬运设备",
        options=["车辆", "电动车", "推车与搬运设备"],
    ),
    AssetCategoryGroup(key="other", label="其他资产", options=["其他"]),
)

BUILT_IN_CATEGORIES = frozenset(option for group in CATEGORY_GROUPS for option in group.options)
