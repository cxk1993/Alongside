"""dashboard 插件包（T11 概览·成长罗盘最小首屏）。

本插件不持有业务表：概览是聚合器，不是数据源。
上游数据一律经 HTTP 调同机 Alongside API（见 aggregator.py）。
"""
