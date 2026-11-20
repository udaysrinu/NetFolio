import CoreGraphics
let opts = CGWindowListOption(arrayLiteral: .optionAll)
let list = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as! [[String: Any]]
for w in list {
  if let owner = w[kCGWindowOwnerName as String] as? String, owner == "Google Chrome",
     let name = w[kCGWindowName as String] as? String, !name.isEmpty,
     let id = w[kCGWindowNumber as String] as? Int, let layer = w[kCGWindowLayer as String] as? Int, layer == 0 {
    print("\(id)\t\(name)")
  }
}
